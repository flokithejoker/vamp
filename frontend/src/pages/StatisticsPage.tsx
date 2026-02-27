import { useEffect, useMemo, useState } from 'react';
import { CallsTimelineChart } from '../components/CallsTimelineChart';
import { StatCard } from '../components/StatCard';
import { fetchJsonWithRetry } from '../lib/http';

type TimelineKey = '1h' | '1d' | '7d' | '1m' | 'total';

type StatisticsOverviewResponse = {
  timeline: TimelineKey;
  currency: string;
  window: {
    startTimeUnix: number | null;
    endTimeUnix: number;
    timezone: string;
  };
  callsSeries: Array<{
    bucketStartUnix: number;
    bucketLabel: string;
    callCount: number;
  }>;
  metrics: {
    totalCalls: number;
    totalCost: {
      amount: number;
      currency: string;
      includedCalls: number;
      excludedNoCurrency: number;
      excludedOtherCurrency: number;
    };
    averageCostPerCall: {
      amount: number | null;
      currency: string;
      includedCalls: number;
    };
    averageDurationSeconds: number | null;
    durationIncludedCalls: number;
    averageRating: number | null;
    ratedCalls: number;
    successRatePercent: number | null;
    successKnownCalls: number;
    successUnknownCalls: number;
  };
  diagnostics: {
    truncated: boolean;
    fetchedCalls: number;
  };
};

const TIMELINE_OPTIONS: Array<{ key: TimelineKey; label: string }> = [
  { key: '1h', label: '1h' },
  { key: '1d', label: '1d' },
  { key: '7d', label: '7d' },
  { key: '1m', label: '1m' },
  { key: 'total', label: 'total' },
];

const DEFAULT_TIMELINE: TimelineKey = '1d';
const DEFAULT_CURRENCY = 'USD';

function formatInteger(value: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value);
}

function formatCost(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds) || seconds < 0) {
    return '-';
  }
  const safeSeconds = Math.round(seconds);
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = safeSeconds % 60;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;

  if (hours > 0) {
    return `${hours}h ${remainingMinutes}m ${remainingSeconds}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${remainingSeconds}s`;
  }
  return `${remainingSeconds}s`;
}

function formatRating(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return '-';
  }
  return value.toFixed(1);
}

function formatPercentInteger(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return '-';
  }
  return `${Math.round(value)}%`;
}

export function StatisticsPage() {
  const [selectedTimeline, setSelectedTimeline] = useState<TimelineKey>(DEFAULT_TIMELINE);
  const [data, setData] = useState<StatisticsOverviewResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function loadStatistics() {
      setIsLoading(true);
      setError(null);

      try {
        const response = await fetchJsonWithRetry<StatisticsOverviewResponse>(
          `/api/statistics/overview?timeline=${selectedTimeline}&currency=${DEFAULT_CURRENCY}`,
          { signal: controller.signal }
        );
        if (cancelled) {
          return;
        }
        setData(response);
      } catch (requestError) {
        if (cancelled) {
          return;
        }
        const message = requestError instanceof Error ? requestError.message : 'Unknown request error.';
        setError(message);
        setData(null);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadStatistics();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [selectedTimeline, reloadTick]);

  const totalCostFootnote = useMemo(() => {
    if (!data) {
      return '';
    }
    const excluded = data.metrics.totalCost.excludedNoCurrency + data.metrics.totalCost.excludedOtherCurrency;
    if (excluded <= 0) {
      return '';
    }
    return `${formatInteger(excluded)} call${excluded === 1 ? '' : 's'} excluded due to currency mismatch/missing.`;
  }, [data]);

  const averageCostPerCallFootnote = useMemo(() => {
    if (!data) {
      return '';
    }
    const included = data.metrics.averageCostPerCall.includedCalls;
    if (included <= 0) {
      return '';
    }
    return `Based on ${formatInteger(included)} call${included === 1 ? '' : 's'} with ${data.metrics.averageCostPerCall.currency} cost values.`;
  }, [data]);

  const averageDurationFootnote = useMemo(() => {
    if (!data) {
      return '';
    }
    const included = data.metrics.durationIncludedCalls;
    if (included <= 0) {
      return '';
    }
    return `Based on ${formatInteger(included)} call${included === 1 ? '' : 's'} with duration metadata.`;
  }, [data]);

  const averageRatingFootnote = useMemo(() => {
    if (!data) {
      return '';
    }
    const rated = data.metrics.ratedCalls;
    if (rated <= 0) {
      return '';
    }
    return `Based on ${formatInteger(rated)} rated call${rated === 1 ? '' : 's'}.`;
  }, [data]);

  const successRateFootnote = useMemo(() => {
    if (!data) {
      return '';
    }
    const known = data.metrics.successKnownCalls;
    const unknown = data.metrics.successUnknownCalls;
    if (known <= 0 && unknown <= 0) {
      return '';
    }
    if (known <= 0) {
      return `${formatInteger(unknown)} call${unknown === 1 ? '' : 's'} have unknown success state.`;
    }
    return `${formatInteger(known)} call${known === 1 ? '' : 's'} with known outcome${
      unknown > 0 ? `, ${formatInteger(unknown)} unknown` : ''
    }.`;
  }, [data]);

  function retryLoad() {
    setReloadTick((previous) => previous + 1);
  }

  return (
    <section className="page-shell stats-page">
      <div className="page-surface page-heading home-hero stats-header">
        <div className="stats-header-row">
          <h2>Statistics</h2>
          <div className="stats-timeline-group" role="tablist" aria-label="Statistics timeline">
            {TIMELINE_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                className={`stats-timeline-button ${selectedTimeline === option.key ? 'is-active' : ''}`}
                onClick={() => setSelectedTimeline(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isLoading ? <p className="monitoring-state">Loading statistics...</p> : null}

      {!isLoading && error ? (
        <div className="monitoring-state monitoring-state-error">
          <p>{error}</p>
          <button type="button" className="btn-secondary monitoring-error-retry" onClick={retryLoad}>
            Retry
          </button>
        </div>
      ) : null}

      {!isLoading && !error && data ? (
        <>
          <div className="page-surface stats-chart-surface">
            <div className="stats-chart-header">
              <h3>Calls Over Time</h3>
              <p>{formatInteger(data.metrics.totalCalls)} total calls in selected timeline</p>
            </div>
            <CallsTimelineChart points={data.callsSeries} />
            {data.diagnostics.truncated ? (
              <p className="stats-inline-note">
                Showing partial totals due to safety fetch cap. Narrow the timeline for full precision.
              </p>
            ) : null}
          </div>

          <div className="stats-card-grid">
            <StatCard
              title="Total Cost"
              value={formatCost(data.metrics.totalCost.amount, data.metrics.totalCost.currency)}
              description={`Summed in ${data.metrics.totalCost.currency} only.`}
              footnote={totalCostFootnote}
            />
            <StatCard
              title="Average Cost Per Call"
              value={
                data.metrics.averageCostPerCall.amount === null
                  ? '-'
                  : formatCost(data.metrics.averageCostPerCall.amount, data.metrics.averageCostPerCall.currency)
              }
              description={`Average in ${data.metrics.averageCostPerCall.currency}.`}
              footnote={
                data.metrics.averageCostPerCall.amount === null
                  ? 'No cost data available.'
                  : averageCostPerCallFootnote
              }
            />
          </div>

          <div className="stats-card-grid">
            <StatCard
              title="Average Duration"
              value={formatDuration(data.metrics.averageDurationSeconds)}
              description="Average length of calls in this timeline."
              footnote={
                data.metrics.averageDurationSeconds === null ? 'No duration data available.' : averageDurationFootnote
              }
            />
            <StatCard
              title="Total Calls"
              value={formatInteger(data.metrics.totalCalls)}
              description="Number of conversations handled in this timeline."
            />
          </div>

          <div className="stats-card-grid">
            <StatCard
              title="Average Rating"
              value={formatRating(data.metrics.averageRating)}
              description="Average customer rating."
              footnote={data.metrics.averageRating === null ? 'No rating data available.' : averageRatingFootnote}
            />
            <StatCard
              title="Success Rate"
              value={formatPercentInteger(data.metrics.successRatePercent)}
              description="Percent of successful calls."
              footnote={data.metrics.successRatePercent === null ? 'No success data available.' : successRateFootnote}
            />
          </div>
        </>
      ) : null}
    </section>
  );
}
