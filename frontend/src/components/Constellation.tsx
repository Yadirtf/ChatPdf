import { useEffect, useRef, useState } from "react";
import type { Source, Star } from "../api";

type Props = {
  stars: Star[];
  forming: { total: number; done: number } | null;
  query: { x: number; y: number } | null;
  hits: Source[];
  focus: number | null;
  onPick?: (star: Star) => void;
};

type Node = { star: Star; x: number; y: number; seed: number };

const INK = "12, 11, 10";
const BONE = "237, 231, 220";
const EMBER = "255, 92, 57";
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Posición de la partícula i en el anillo de "formación" mientras se vectoriza el PDF. */
function ringPos(i: number, total: number, t: number) {
  const a = (i / Math.max(total, 1)) * Math.PI * 2 + t * 0.00035;
  const r = 0.42 + Math.sin(t * 0.002 + i * 1.7) * 0.04;
  return { x: Math.cos(a) * r, y: Math.sin(a) * r };
}

export default function Constellation({ stars, forming, query, hits, focus, onPick }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const nodes = useRef<Map<number, Node>>(new Map());
  const props = useRef({ forming, query, hits, focus });
  const hitAt = useRef<number>(0);
  const mouse = useRef<{ x: number; y: number } | null>(null);
  const [hover, setHover] = useState<{ star: Star; px: number; py: number } | null>(null);

  props.current = { forming, query, hits, focus };

  useEffect(() => {
    hitAt.current = performance.now();
  }, [query]);

  // Sincroniza nodos: los nuevos nacen en el anillo y viajan a su posición real.
  useEffect(() => {
    const map = nodes.current;
    const ids = new Set(stars.map((s) => s.id));
    for (const id of [...map.keys()]) if (!ids.has(id)) map.delete(id);
    const t = performance.now();
    stars.forEach((s, i) => {
      const n = map.get(s.id);
      if (n) n.star = s;
      else {
        const p = ringPos(i, stars.length, t);
        map.set(s.id, { star: s, x: p.x, y: p.y, seed: Math.random() * 1000 });
      }
    });
  }, [stars]);

  useEffect(() => {
    const cv = canvas.current!;
    const ctx = cv.getContext("2d")!;
    let raf = 0;
    let W = 0;
    let H = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = cv.clientWidth;
      H = cv.clientHeight;
      cv.width = W * dpr;
      cv.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(cv);
    resize();

    const frame = (t: number) => {
      const still = reduceMotion();
      const { forming, query, hits, focus } = props.current;
      const R = Math.min(W, H) * 0.4;
      const cx = W / 2;
      const cy = H / 2;
      const P = (x: number, y: number) => [cx + x * R, cy - y * R] as const;

      ctx.clearRect(0, 0, W, H);

      // Retícula de observatorio
      ctx.lineWidth = 1;
      for (const k of [0.33, 0.66, 1]) {
        ctx.strokeStyle = `rgba(${BONE}, ${k === 1 ? 0.09 : 0.05})`;
        ctx.setLineDash(k === 1 ? [] : [2, 6]);
        ctx.beginPath();
        ctx.arc(cx, cy, R * k, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.strokeStyle = `rgba(${BONE}, 0.05)`;
      ctx.beginPath();
      ctx.moveTo(cx - R * 1.15, cy);
      ctx.lineTo(cx + R * 1.15, cy);
      ctx.moveTo(cx, cy - R * 1.15);
      ctx.lineTo(cx, cy + R * 1.15);
      ctx.stroke();
      ctx.fillStyle = `rgba(${BONE}, 0.22)`;
      ctx.font = "10px 'Geist Mono', monospace";
      for (let deg = 0; deg < 360; deg += 30) {
        const a = (deg * Math.PI) / 180;
        ctx.fillText(String(deg).padStart(3, "0"), cx + Math.cos(a) * (R + 14) - 9, cy - Math.sin(a) * (R + 14) + 3);
      }

      // Anillo de formación durante la indexación
      if (forming && forming.total > 0) {
        for (let i = 0; i < forming.total; i++) {
          const p = ringPos(i, forming.total, still ? 0 : t);
          const [x, y] = P(p.x, p.y);
          const lit = i < forming.done;
          ctx.fillStyle = lit ? `rgba(${EMBER}, 0.95)` : `rgba(${BONE}, 0.25)`;
          ctx.beginPath();
          ctx.arc(x, y, lit ? 2.4 : 1.4, 0, Math.PI * 2);
          ctx.fill();
          if (lit) {
            ctx.fillStyle = `rgba(${EMBER}, 0.12)`;
            ctx.beginPath();
            ctx.arc(x, y, 8, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }

      // Mover nodos hacia su destino con una leve deriva
      const list = [...nodes.current.values()].sort((a, b) => a.star.idx - b.star.idx);
      for (const n of list) {
        const drift = still ? 0 : 0.006;
        const tx = n.star.x + Math.sin(t * 0.0006 + n.seed) * drift;
        const ty = n.star.y + Math.cos(t * 0.0005 + n.seed) * drift;
        n.x += (tx - n.x) * (still ? 1 : 0.045);
        n.y += (ty - n.y) * (still ? 1 : 0.045);
      }

      // Hilo de lectura: une los fragmentos en el orden del documento
      if (list.length > 1) {
        ctx.strokeStyle = `rgba(${BONE}, 0.09)`;
        ctx.beginPath();
        list.forEach((n, i) => {
          const [x, y] = P(n.x, n.y);
          if (i) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
        });
        ctx.stroke();
      }

      // Rayos desde la consulta hacia los fragmentos recuperados
      const hitRank = new Map(hits.map((h, i) => [h.chunk_id, i]));
      if (query) {
        const since = still ? 1e9 : t - hitAt.current;
        const [qx, qy] = P(query.x, query.y);
        hits.forEach((h, i) => {
          const n = nodes.current.get(h.chunk_id);
          if (!n) return;
          const [x, y] = P(n.x, n.y);
          const prog = Math.min(1, Math.max(0, (since - i * 90) / 500));
          const a = 0.25 + Math.max(0, h.score) * 0.6;
          ctx.strokeStyle = `rgba(${EMBER}, ${a})`;
          ctx.lineWidth = i === 0 ? 1.6 : 1;
          ctx.setLineDash(i === 0 ? [] : [3, 4]);
          ctx.beginPath();
          ctx.moveTo(qx, qy);
          ctx.lineTo(qx + (x - qx) * prog, qy + (y - qy) * prog);
          ctx.stroke();
        });
        ctx.setLineDash([]);
        ctx.lineWidth = 1;
        const pulse = still ? 0 : (t % 1800) / 1800;
        ctx.strokeStyle = `rgba(${EMBER}, ${0.6 * (1 - pulse)})`;
        ctx.beginPath();
        ctx.arc(qx, qy, 6 + pulse * 22, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = `rgb(${EMBER})`;
        ctx.save();
        ctx.translate(qx, qy);
        ctx.rotate(Math.PI / 4);
        ctx.fillRect(-4, -4, 8, 8);
        ctx.restore();
        ctx.fillStyle = `rgba(${EMBER}, 0.9)`;
        ctx.font = "10px 'Geist Mono', monospace";
        ctx.fillText("TU PREGUNTA", qx + 12, qy - 10);
      }

      // Estrellas
      let near: { n: Node; d: number; x: number; y: number } | null = null;
      for (const n of list) {
        const [x, y] = P(n.x, n.y);
        const rank = hitRank.get(n.star.id);
        const isHit = rank !== undefined;
        const isFocus = focus === n.star.id;
        const tw = still ? 1 : 0.75 + Math.sin(t * 0.003 + n.seed) * 0.25;
        const r = isHit ? 3.6 - rank * 0.25 : 1.8;

        ctx.fillStyle = isHit ? `rgba(${EMBER}, 0.14)` : `rgba(${BONE}, ${0.06 * tw})`;
        ctx.beginPath();
        ctx.arc(x, y, r * 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = isHit ? `rgb(${EMBER})` : `rgba(${BONE}, ${0.55 + 0.45 * tw})`;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();

        if (isHit) {
          ctx.fillStyle = `rgba(${EMBER}, 0.95)`;
          ctx.font = "10px 'Geist Mono', monospace";
          ctx.fillText(`${rank + 1}·p${n.star.page}`, x + 8, y + 3);
        }
        if (isFocus) {
          ctx.strokeStyle = `rgba(${BONE}, 0.9)`;
          ctx.beginPath();
          ctx.arc(x, y, 11, 0, Math.PI * 2);
          ctx.stroke();
        }
        if (mouse.current) {
          const d = Math.hypot(mouse.current.x - x, mouse.current.y - y);
          if (d < 14 && (!near || d < near.d)) near = { n, d, x, y };
        }
      }

      if (near) {
        ctx.strokeStyle = `rgba(${BONE}, 0.7)`;
        ctx.beginPath();
        ctx.arc(near.x, near.y, 8, 0, Math.PI * 2);
        ctx.stroke();
      }
      setHover((prev) => {
        if (!near) return prev ? null : prev;
        if (prev && prev.star.id === near.n.star.id) return prev;
        return { star: near.n.star, px: near.x, py: near.y };
      });

      // Velo para fundir el borde con el fondo
      const g = ctx.createRadialGradient(cx, cy, R * 0.9, cx, cy, Math.max(W, H) * 0.75);
      g.addColorStop(0, `rgba(${INK}, 0)`);
      g.addColorStop(1, `rgba(${INK}, 0.85)`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  return (
    <div className="constellation">
      <canvas
        ref={canvas}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          mouse.current = { x: e.clientX - r.left, y: e.clientY - r.top };
        }}
        onMouseLeave={() => (mouse.current = null)}
        onClick={() => hover && onPick?.(hover.star)}
        style={{ cursor: hover ? "pointer" : "crosshair" }}
        aria-label="Mapa de fragmentos del documento"
        role="img"
      />
      {hover && (
        <div className="star-tip" style={{ left: hover.px, top: hover.py }}>
          <span className="mono">
            frag {String(hover.star.idx + 1).padStart(2, "0")} · pág {hover.star.page}
          </span>
          <p>{hover.star.preview}…</p>
        </div>
      )}
    </div>
  );
}
