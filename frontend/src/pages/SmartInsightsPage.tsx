import { useEffect, useState } from 'react';
import { fetchJsonWithRetry } from '../lib/http';

type TimelineKey = '1d' | '7d' | '1m';
type OperationalStatus = 'stable' | 'watch' | 'at_risk';

const DEFAULT_TIMELINE: TimelineKey = '7d';
const TIMELINE_OPTIONS: Array<{ key: TimelineKey; label: string }> = [
  { key: '1d', label: '1d' },
  { key: '7d', label: '7d' },
  { key: '1m', label: '1m' },
];

type SmartInsightsReportResponse = {
  meta: {
    reportVersion: 2;
    timeline: TimelineKey;
    generatedAtIso: string;
    totalCalls: number;
    availableCalls: number;
    analyzedCalls: number;
    detailFetchCap: number;
    cappedByDetailCap: boolean;
    detailFetchFailures: number;
    dataCoveragePercent: number;
  };
  overview: {
    summary: string;
    operationalStatus: OperationalStatus;
    topOpportunity: string;
  };
  knowledgeGapInsights: Array<{
    knowledgeGapLabel: string;
    primaryFrictionPointLabel: string;
    recommendedInternalActionLabel: string;
    conciseExplanation: string;
    evidence: {
      calls: number;
      sharePercent: number;
    };
  }>;
  failureTypeInsights: Array<{
    failureTypeLabel: string;
    whyItHappens: string;
    evidence: {
      calls: number;
      sharePercent: number;
    };
    relatedFriction: string;
    relatedKnowledgeGap: string;
  }>;
  priorityActionQueue: Array<{
    priority: number;
    actionTitle: string;
    whyNow: string;
    agentNextStep: string;
    escalationTrigger: string;
    appliesTo: string;
    evidence: {
      calls: number;
      sharePercent: number;
    };
  }>;
  caveats: string[];
};

function formatInteger(value: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value);
}

function formatPercent(value: number): string {
  return `${Math.round(value)}%`;
}

function formatDateTime(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.valueOf())) {
    return '-';
  }
  return new Intl.DateTimeFormat('de-DE', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed);
}

function formatStatus(value: OperationalStatus): string {
  if (value === 'at_risk') {
    return 'At Risk';
  }
  if (value === 'watch') {
    return 'Watch';
  }
  return 'Stable';
}

export function SmartInsightsPage() {
  const [selectedTimeline, setSelectedTimeline] = useState<TimelineKey>(DEFAULT_TIMELINE);
  const [data, setData] = useState<SmartInsightsReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function loadReport() {
      setIsLoading(true);
      setError(null);

      try {
        const response = await fetchJsonWithRetry<SmartInsightsReportResponse>(
          `/api/smart-insights/report?timeline=${selectedTimeline}`,
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

    void loadReport();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [selectedTimeline, reloadTick]);

  function refreshReport() {
    setReloadTick((previous) => previous + 1);
  }

  return (
    <section className="page-shell smart-insights-page">
      <div className="page-surface page-heading home-hero smart-header">
        <div className="smart-header-row">
          <div>
            <h2>Smart Insights</h2>
            {data ? (
              <p className="smart-generated-at">
                Generated {formatDateTime(data.meta.generatedAtIso)} • Analyzed {formatInteger(data.meta.analyzedCalls)} of{' '}
                {formatInteger(data.meta.availableCalls)} calls
                {data.meta.cappedByDetailCap ? ` (cap ${formatInteger(data.meta.detailFetchCap)})` : ''}
              </p>
            ) : null}
          </div>
          <div className="stats-timeline-group" role="tablist" aria-label="Smart Insights timeline">
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

      {isLoading ? <p className="monitoring-state">Loading smart insights...</p> : null}

      {!isLoading && error ? (
        <div className="monitoring-state monitoring-state-error">
          <p>{error}</p>
          <button type="button" className="btn-secondary monitoring-error-retry" onClick={refreshReport}>
            Retry
          </button>
        </div>
      ) : null}

      {!isLoading && !error && data ? (
        <article className="page-surface smart-report-sheet">
          <section className="smart-report-section">
            <div className="smart-report-section-head">
              <h3>Executive Summary</h3>
              <span className={`smart-status-chip smart-status-${data.overview.operationalStatus}`}>
                {formatStatus(data.overview.operationalStatus)}
              </span>
            </div>
            <p className="smart-report-summary">{data.overview.summary}</p>
            <p className="smart-report-opportunity">
              <strong>Top opportunity:</strong> {data.overview.topOpportunity}
            </p>
          </section>

          <section className="smart-report-section">
            <div className="smart-report-section-head">
              <h3>Main Knowledge Gaps</h3>
              <p>Most frequent patterns from recent calls.</p>
            </div>

            {data.knowledgeGapInsights.length > 0 ? (
              <div className="smart-insight-list">
                {data.knowledgeGapInsights.slice(0, 3).map((item, index) => (
                  <article key={`${item.knowledgeGapLabel}-${item.primaryFrictionPointLabel}-${index}`} className="smart-insight-item">
                    <div className="smart-insight-head">
                      <span className="smart-insight-rank">#{index + 1}</span>
                      <div className="smart-insight-tags">
                        <span className="smart-insight-tag">Gap: {item.knowledgeGapLabel}</span>
                        <span className="smart-insight-tag">Friction: {item.primaryFrictionPointLabel}</span>
                        <span className="smart-insight-tag">Action: {item.recommendedInternalActionLabel}</span>
                      </div>
                    </div>
                    <p className="smart-insight-text">{item.conciseExplanation}</p>
                    <p className="smart-insight-evidence">
                      Evidence: {formatInteger(item.evidence.calls)} calls ({formatPercent(item.evidence.sharePercent)})
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <p className="smart-empty-note">No clear knowledge-gap pattern in this window.</p>
            )}
          </section>

          <section className="smart-report-section">
            <div className="smart-report-section-head">
              <h3>Most Common Failure Types</h3>
              <p>What fails most often and why.</p>
            </div>

            {data.failureTypeInsights.length > 0 ? (
              <div className="smart-insight-list">
                {data.failureTypeInsights.slice(0, 3).map((item, index) => (
                  <article key={`${item.failureTypeLabel}-${index}`} className="smart-insight-item">
                    <div className="smart-insight-head">
                      <span className="smart-insight-rank">#{index + 1}</span>
                      <h4 className="smart-insight-title">{item.failureTypeLabel}</h4>
                    </div>
                    <p className="smart-insight-text">{item.whyItHappens}</p>
                    <p className="smart-insight-meta">
                      Related friction: {item.relatedFriction} • Related knowledge gap: {item.relatedKnowledgeGap}
                    </p>
                    <p className="smart-insight-evidence">
                      Evidence: {formatInteger(item.evidence.calls)} calls ({formatPercent(item.evidence.sharePercent)})
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <p className="smart-empty-note">No dominant failure type in this window.</p>
            )}
          </section>

          <section className="smart-report-section">
            <div className="smart-report-section-head">
              <h3>Priority Action Queue</h3>
              <p>Top next actions for frontline support agents.</p>
            </div>

            {data.priorityActionQueue.length > 0 ? (
              <div className="smart-action-queue">
                {data.priorityActionQueue.slice(0, 3).map((action) => (
                  <article key={`${action.priority}-${action.actionTitle}`} className="smart-action-item-v2">
                    <div className="smart-action-head-v2">
                      <span className="smart-action-priority-v2">#{action.priority}</span>
                      <h4>{action.actionTitle}</h4>
                    </div>
                    <p className="smart-action-line-v2">
                      <strong>Why now:</strong> {action.whyNow}
                    </p>
                    <p className="smart-action-line-v2">
                      <strong>Agent next step:</strong> {action.agentNextStep}
                    </p>
                    <p className="smart-action-line-v2">
                      <strong>Escalation trigger:</strong> {action.escalationTrigger}
                    </p>
                    <p className="smart-action-line-v2">
                      <strong>Applies to:</strong> {action.appliesTo}
                    </p>
                    <p className="smart-insight-evidence">
                      Evidence: {formatInteger(action.evidence.calls)} calls ({formatPercent(action.evidence.sharePercent)})
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <p className="smart-empty-note">No priority actions available for this window.</p>
            )}
          </section>

          <section className="smart-report-section">
            <div className="smart-report-section-head">
              <h3>Caveats</h3>
              <p>Important context to keep in mind.</p>
            </div>
            {data.caveats.length > 0 ? (
              <ul className="smart-caveats-list">
                {data.caveats.map((caveat) => (
                  <li key={caveat}>{caveat}</li>
                ))}
              </ul>
            ) : (
              <p className="smart-empty-note">No major caveats for this period.</p>
            )}
          </section>
        </article>
      ) : null}
    </section>
  );
}
