import { useState, type ReactNode } from "react";

/** Painel flutuante recolhível; no celular começa recolhido. */
export default function Panel({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(() => window.innerWidth > 600);
  return (
    <div className={`panel${open ? "" : " collapsed"}`}>
      <div className="panel-head">
        <h1>{title}</h1>
        <button className="panel-btn" onClick={() => setOpen(!open)} aria-label={open ? "Recolher painel" : "Abrir painel"}>
          {open ? "–" : "☰"}
        </button>
      </div>
      <div hidden={!open}>{children}</div>
    </div>
  );
}
