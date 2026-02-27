type CallsSeriesPoint = {
  bucketStartUnix: number;
  bucketLabel: string;
  callCount: number;
};

type CallsTimelineChartProps = {
  points: CallsSeriesPoint[];
};

const CHART_WIDTH = 720;
const CHART_HEIGHT = 240;
const PADDING = { top: 16, right: 20, bottom: 30, left: 20 };

function clampToTwoDecimals(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function CallsTimelineChart({ points }: CallsTimelineChartProps) {
  const plotWidth = CHART_WIDTH - PADDING.left - PADDING.right;
  const plotHeight = CHART_HEIGHT - PADDING.top - PADDING.bottom;

  const maxCalls = Math.max(1, ...points.map((point) => point.callCount));
  const safePoints = points.length > 0 ? points : [{ bucketStartUnix: 0, bucketLabel: '-', callCount: 0 }];

  const plotted = safePoints.map((point, index) => {
    const ratioX = safePoints.length <= 1 ? 0.5 : index / (safePoints.length - 1);
    const x = PADDING.left + ratioX * plotWidth;
    const y = PADDING.top + (1 - point.callCount / maxCalls) * plotHeight;
    return { ...point, x, y };
  });

  const linePath = plotted.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');
  const areaPath = `${linePath} L ${plotted[plotted.length - 1].x} ${PADDING.top + plotHeight} L ${plotted[0].x} ${
    PADDING.top + plotHeight
  } Z`;

  const xTicks: Array<{ index: number; label: string; x: number }> = [];
  if (plotted.length === 1) {
    xTicks.push({ index: 0, label: plotted[0].bucketLabel, x: plotted[0].x });
  } else {
    const sampleIndexes = [0, Math.floor((plotted.length - 1) / 2), plotted.length - 1];
    const uniqueIndexes = Array.from(new Set(sampleIndexes));
    for (const idx of uniqueIndexes) {
      xTicks.push({ index: idx, label: plotted[idx].bucketLabel, x: plotted[idx].x });
    }
  }

  const yTicks = [0, maxCalls / 2, maxCalls];

  return (
    <div className="stats-chart-wrap" role="img" aria-label="Calls timeline chart">
      <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} className="stats-chart-svg">
        <defs>
          <linearGradient id="stats-area-gradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(14, 165, 233, 0.35)" />
            <stop offset="100%" stopColor="rgba(14, 165, 233, 0.02)" />
          </linearGradient>
        </defs>

        <rect
          x={PADDING.left}
          y={PADDING.top}
          width={plotWidth}
          height={plotHeight}
          className="stats-chart-plot-bg"
          rx="12"
        />

        {yTicks.map((value) => {
          const y = PADDING.top + (1 - value / maxCalls) * plotHeight;
          return (
            <g key={value}>
              <line x1={PADDING.left} y1={y} x2={PADDING.left + plotWidth} y2={y} className="stats-chart-grid-line" />
              <text x={PADDING.left + 6} y={y - 4} className="stats-chart-y-label">
                {clampToTwoDecimals(value)}
              </text>
            </g>
          );
        })}

        <path d={areaPath} className="stats-chart-area" />
        <path d={linePath} className="stats-chart-line" />

        {plotted.map((point) => (
          <circle key={point.bucketStartUnix} cx={point.x} cy={point.y} r="3.2" className="stats-chart-point" />
        ))}

        {xTicks.map((tick) => (
          <text key={tick.index} x={tick.x} y={CHART_HEIGHT - 8} textAnchor="middle" className="stats-chart-x-label">
            {tick.label}
          </text>
        ))}
      </svg>
    </div>
  );
}
