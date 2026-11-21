import { useEffect, useRef } from "react";
import { X } from "lucide-react";

/** Janela modal com foco preso, Esc para fechar e foco devolvido ao botão que abriu. */
export function Modal({ title, onClose, children, wide, className }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>("input, select, button:not(.modal-x)")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); close.current(); return; }
      if (e.key !== "Tab" || !box.current) return;
      const f = [...box.current.querySelectorAll<HTMLElement>("a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea")];
      if (!f.length) return;
      const [first, last] = [f[0], f[f.length - 1]];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, []);
  return (
    <div className={`modal-back ${className ?? ""}`} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="modal-title" ref={box}>
        <div className="modal-head">
          <h2 id="modal-title">{title}</h2>
          <button className="ghost sm modal-x" onClick={onClose} aria-label="Fechar"><X size={16} aria-hidden="true" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}
