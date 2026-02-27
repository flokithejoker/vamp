export type RouteIconName =
  | 'home'
  | 'settings'
  | 'monitoring'
  | 'statistics'
  | 'feedback'
  | 'smartInsights';

type RouteIconProps = {
  name: RouteIconName;
  className?: string;
};

const svgProps = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

export function RouteIcon({ name, className }: RouteIconProps) {
  switch (name) {
    case 'home':
      return (
        <svg className={className} {...svgProps}>
          <path d="M3 10.5L12 3l9 7.5" />
          <path d="M5 9.8V21h14V9.8" />
          <path d="M10 21v-6h4v6" />
        </svg>
      );
    case 'settings':
      return (
        <svg className={className} {...svgProps}>
          <path d="M4 6h7" />
          <circle cx="14" cy="6" r="2" />
          <path d="M4 12h11" />
          <circle cx="18" cy="12" r="2" />
          <path d="M4 18h5" />
          <circle cx="11" cy="18" r="2" />
        </svg>
      );
    case 'monitoring':
      return (
        <svg className={className} {...svgProps}>
          <rect x="3" y="4" width="18" height="16" rx="3" />
          <path d="M7 14l2.3-2.3L12 14l3-3 2 2" />
          <path d="M7 9h10" />
        </svg>
      );
    case 'statistics':
      return (
        <svg className={className} {...svgProps}>
          <path d="M4 20h16" />
          <rect x="6" y="11" width="3" height="7" rx="1" />
          <rect x="11" y="8" width="3" height="10" rx="1" />
          <rect x="16" y="5" width="3" height="13" rx="1" />
        </svg>
      );
    case 'feedback':
      return (
        <svg className={className} {...svgProps}>
          <path d="M6 18l-3 3V6a3 3 0 013-3h12a3 3 0 013 3v9a3 3 0 01-3 3H6z" />
          <path d="M8 8h8" />
          <path d="M8 12h5" />
        </svg>
      );
    case 'smartInsights':
      return (
        <svg className={className} {...svgProps}>
          <path d="M4 19h16" />
          <path d="M6 14l3-3 3 2 4-5 2 2" />
          <path d="M18 5l.8 1.7L20.5 7.5l-1.7.8L18 10l-.8-1.7-1.7-.8 1.7-.8L18 5z" />
        </svg>
      );
    default:
      return null;
  }
}

export function ChevronLeftIcon({ className }: { className?: string }) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M15 18l-6-6 6-6" />
    </svg>
  );
}

export function ChevronRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}

export function ArrowUpRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M7 17L17 7" />
      <path d="M8 7h9v9" />
    </svg>
  );
}
