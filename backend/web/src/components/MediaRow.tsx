import { useState, type ReactNode } from "react";

export function CoverArt({ url, alt, size = 56 }: { url?: string | null; alt: string; size?: number }) {
  const [broken, setBroken] = useState(false);
  if (!url || broken) {
    return (
      <div className="cover placeholder" style={{ width: size, height: size }} aria-label={alt}>
        ♪
      </div>
    );
  }
  return (
    <img
      className="cover"
      src={url}
      alt={alt}
      style={{ width: size, height: size }}
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}

export function MediaRow({
  imageUrl,
  title,
  subtitle,
  trailing,
  onClick,
}: {
  imageUrl?: string | null;
  title: string;
  subtitle?: string;
  trailing?: ReactNode;
  onClick?: () => void;
}) {
  return (
    <div className={"row" + (onClick ? " clickable" : "")} onClick={onClick}>
      <CoverArt url={imageUrl} alt={`Cover art for ${title}`} />
      <div className="meta">
        <div className="title">{title}</div>
        {subtitle && <div className="sub">{subtitle}</div>}
      </div>
      {trailing && (
        <div className="trailing" onClick={(e) => e.stopPropagation()}>
          {trailing}
        </div>
      )}
    </div>
  );
}
