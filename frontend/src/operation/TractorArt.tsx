/** Ilustração própria de um trator genérico (vista lateral): carroceria verde, pneus escuros, cubos claros.
 *  Sem marca, sem logotipo e sem o esquema verde + amarelo de fabricante. Decorativa (aria-hidden). */
export function TractorArt({ width = 150, className }: { width?: number; className?: string }) {
  const tread = (cx: number, cy: number, r: number, n: number) =>
    Array.from({ length: n }, (_, i) => {
      const a = (i / n) * Math.PI * 2;
      return <rect key={i} x={cx - 2.2} y={cy - r - 1.5} width={4.4} height={6} rx={1.2} transform={`rotate(${(a * 180) / Math.PI} ${cx} ${cy})`} />;
    });
  return (
    <svg className={className} width={width} height={(width * 130) / 210} viewBox="0 0 210 130" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="agf-tr-body" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#52B860" />
          <stop offset=".55" stopColor="#3A9A48" />
          <stop offset="1" stopColor="#2A7637" />
        </linearGradient>
        <linearGradient id="agf-tr-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5E8F87" stopOpacity=".85" />
          <stop offset=".5" stopColor="#24433E" stopOpacity=".92" />
          <stop offset="1" stopColor="#162B28" />
        </linearGradient>
        <radialGradient id="agf-tr-rim" cx=".4" cy=".35" r=".75">
          <stop offset="0" stopColor="#F1F4EF" />
          <stop offset="1" stopColor="#AEB8B0" />
        </radialGradient>
      </defs>
      {/* sombra no chão */}
      <ellipse cx="108" cy="124" rx="92" ry="4.5" fill="#000" opacity=".28" />
      {/* chassi */}
      <rect x="34" y="84" width="118" height="12" rx="3" fill="#1E2A27" />
      {/* escapamento */}
      <rect x="92" y="22" width="6" height="42" rx="2" fill="#3A4542" />
      <rect x="91" y="19" width="8" height="5" rx="1.5" fill="#262F2D" />
      {/* cabine: colunas, vidros e teto */}
      <path d="M116 30 L172 30 L176 82 L112 82 Z" fill="#20302C" />
      <path d="M121 35 L144 35 L144 70 L118 70 Z" fill="url(#agf-tr-glass)" />
      <path d="M149 35 L168 35 L171 70 L149 70 Z" fill="url(#agf-tr-glass)" />
      <path d="M123 37 L131 37 L122 60 Z" fill="#FFFFFF" opacity=".18" />
      <rect x="110" y="22" width="70" height="10" rx="4" fill="url(#agf-tr-body)" />
      <rect x="114" y="20" width="62" height="4" rx="2" fill="#5FC46C" />
      {/* capô e motor */}
      <path d="M24 60 Q24 54 31 54 L112 54 L114 86 L26 86 Q22 86 22 80 Z" fill="url(#agf-tr-body)" />
      <path d="M30 57 L108 57" stroke="#78D483" strokeWidth="2" strokeLinecap="round" opacity=".7" />
      {/* grade frontal e farol */}
      <rect x="20" y="62" width="9" height="22" rx="2" fill="#17201E" />
      <path d="M22 66h5M22 70h5M22 74h5M22 78h5" stroke="#3F4D49" strokeWidth="1.4" />
      <rect x="31" y="61" width="9" height="5" rx="2" fill="#EAF2E4" opacity=".9" />
      {/* aberturas laterais do motor */}
      <path d="M52 66h36M52 71h36M52 76h36" stroke="#256B31" strokeWidth="2" strokeLinecap="round" />
      {/* degraus */}
      <rect x="114" y="86" width="12" height="3" rx="1" fill="#3A4542" />
      <rect x="116" y="92" width="10" height="3" rx="1" fill="#3A4542" />
      {/* pneu traseiro */}
      <g fill="#141A19">{tread(160, 88, 38, 22)}</g>
      <circle cx="160" cy="88" r="37" fill="#1B2321" />
      <circle cx="160" cy="88" r="30" fill="#232D2B" />
      <circle cx="160" cy="88" r="21" fill="url(#agf-tr-rim)" />
      <circle cx="160" cy="88" r="21" fill="none" stroke="#7D8A83" strokeWidth="1.2" />
      {[0, 60, 120, 180, 240, 300].map((a) => <circle key={a} cx={160 + 13 * Math.cos((a * Math.PI) / 180)} cy={88 + 13 * Math.sin((a * Math.PI) / 180)} r="2" fill="#8E9A93" />)}
      <circle cx="160" cy="88" r="7" fill="#DDE3DC" stroke="#8E9A93" strokeWidth="1.5" />
      {/* para-lama traseiro */}
      <path d="M118 80 Q120 44 160 44 Q200 44 203 80 L196 80 Q193 52 160 52 Q127 52 125 80 Z" fill="url(#agf-tr-body)" />
      {/* pneu dianteiro */}
      <g fill="#141A19">{tread(52, 100, 24, 16)}</g>
      <circle cx="52" cy="100" r="23.5" fill="#1B2321" />
      <circle cx="52" cy="100" r="18" fill="#232D2B" />
      <circle cx="52" cy="100" r="12.5" fill="url(#agf-tr-rim)" />
      <circle cx="52" cy="100" r="12.5" fill="none" stroke="#7D8A83" strokeWidth="1" />
      <circle cx="52" cy="100" r="4.5" fill="#DDE3DC" stroke="#8E9A93" strokeWidth="1.2" />
      {/* para-lama dianteiro */}
      <path d="M28 92 Q30 72 52 72 Q74 72 76 92 L71 92 Q69 78 52 78 Q35 78 33 92 Z" fill="#2F8A3D" />
    </svg>
  );
}
