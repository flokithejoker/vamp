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
const SUPPORT_BOOKING_URL = (import.meta.env.VITE_SUPPORT_BOOKING_URL as string | undefined)?.trim() ?? '';
const SMART_INSIGHTS_CACHE_STORAGE_KEY = 'smart_insights_report_cache_v2';
const SMART_INSIGHTS_TIMELINE_STORAGE_KEY = 'smart_insights_selected_timeline_v2';

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

type SmartInsightsReportCache = Partial<Record<TimelineKey, SmartInsightsReportResponse>>;

function isTimelineKey(value: string): value is TimelineKey {
  return value === '1d' || value === '7d' || value === '1m';
}

function readStoredTimeline(): TimelineKey {
  if (typeof window === 'undefined') {
    return DEFAULT_TIMELINE;
  }
  const rawValue = window.localStorage.getItem(SMART_INSIGHTS_TIMELINE_STORAGE_KEY);
  if (rawValue && isTimelineKey(rawValue)) {
    return rawValue;
  }
  return DEFAULT_TIMELINE;
}

function writeStoredTimeline(timeline: TimelineKey) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(SMART_INSIGHTS_TIMELINE_STORAGE_KEY, timeline);
}

function readStoredReportCache(): SmartInsightsReportCache {
  if (typeof window === 'undefined') {
    return {};
  }
  const rawValue = window.localStorage.getItem(SMART_INSIGHTS_CACHE_STORAGE_KEY);
  if (!rawValue) {
    return {};
  }
  try {
    const parsedValue = JSON.parse(rawValue) as SmartInsightsReportCache | null;
    if (!parsedValue || typeof parsedValue !== 'object') {
      return {};
    }
    const cache: SmartInsightsReportCache = {};
    TIMELINE_OPTIONS.forEach((option) => {
      const report = parsedValue[option.key];
      if (report && typeof report === 'object' && report.meta?.timeline === option.key) {
        cache[option.key] = report;
      }
    });
    return cache;
  } catch {
    return {};
  }
}

function writeStoredReportCache(cache: SmartInsightsReportCache) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(SMART_INSIGHTS_CACHE_STORAGE_KEY, JSON.stringify(cache));
}

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

function buildReportClipboardText(report: SmartInsightsReportResponse): string {
  const lines: string[] = [];
  lines.push('Smart Insights Report');
  lines.push(`Timeline: ${report.meta.timeline}`);
  lines.push(`Generated: ${formatDateTime(report.meta.generatedAtIso)}`);
  lines.push(`Analyzed Calls: ${formatInteger(report.meta.analyzedCalls)} of ${formatInteger(report.meta.availableCalls)}`);
  lines.push('');

  lines.push('Executive Summary');
  lines.push(report.overview.summary);
  lines.push(`Top Opportunity: ${report.overview.topOpportunity}`);
  lines.push('');

  if (report.knowledgeGapInsights.length > 0) {
    lines.push('Main Knowledge Gaps');
    report.knowledgeGapInsights.slice(0, 3).forEach((item, index) => {
      lines.push(
        `${index + 1}. Gap: ${item.knowledgeGapLabel} | Friction: ${item.primaryFrictionPointLabel} | Recommended Action: ${item.recommendedInternalActionLabel}`
      );
      lines.push(`   ${item.conciseExplanation}`);
      lines.push(`   Evidence: ${formatInteger(item.evidence.calls)} calls (${formatPercent(item.evidence.sharePercent)})`);
    });
    lines.push('');
  }

  if (report.failureTypeInsights.length > 0) {
    lines.push('Most Common Failure Types');
    report.failureTypeInsights.slice(0, 3).forEach((item, index) => {
      lines.push(`${index + 1}. ${item.failureTypeLabel}`);
      lines.push(`   Why: ${item.whyItHappens}`);
      lines.push(`   Related friction: ${item.relatedFriction}`);
      lines.push(`   Related knowledge gap: ${item.relatedKnowledgeGap}`);
      lines.push(`   Evidence: ${formatInteger(item.evidence.calls)} calls (${formatPercent(item.evidence.sharePercent)})`);
    });
    lines.push('');
  }

  if (report.priorityActionQueue.length > 0) {
    lines.push('Priority Action Queue');
    report.priorityActionQueue.slice(0, 3).forEach((action, index) => {
      lines.push(`${index + 1}. ${action.actionTitle}`);
      lines.push(`   Why now: ${action.whyNow}`);
      lines.push(`   Agent next step: ${action.agentNextStep}`);
      lines.push(`   Escalation trigger: ${action.escalationTrigger}`);
      lines.push(`   Applies to: ${action.appliesTo}`);
      lines.push(`   Evidence: ${formatInteger(action.evidence.calls)} calls (${formatPercent(action.evidence.sharePercent)})`);
    });
    lines.push('');
  }

  if (report.caveats.length > 0) {
    lines.push('Caveats');
    report.caveats.forEach((caveat) => {
      lines.push(`- ${caveat}`);
    });
  }

  return lines.join('\n').trim();
}

export function SmartInsightsPage() {
  const [selectedTimeline, setSelectedTimeline] = useState<TimelineKey>(() => readStoredTimeline());
  const [reportCache, setReportCache] = useState<SmartInsightsReportCache>(() => readStoredReportCache());
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle');
  const data = reportCache[selectedTimeline] ?? null;

  useEffect(() => {
    writeStoredTimeline(selectedTimeline);
    setError(null);
    setCopyState('idle');
  }, [selectedTimeline]);

  useEffect(() => {
    if (copyState === 'idle') {
      return;
    }
    const timer = window.setTimeout(() => {
      setCopyState('idle');
    }, 2200);
    return () => window.clearTimeout(timer);
  }, [copyState]);

  async function generateReport() {
    const timeline = selectedTimeline;
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetchJsonWithRetry<SmartInsightsReportResponse>(
        `/api/smart-insights/report?timeline=${timeline}`
      );
      setReportCache((previousCache) => {
        const nextCache: SmartInsightsReportCache = {
          ...previousCache,
          [timeline]: response,
        };
        writeStoredReportCache(nextCache);
        return nextCache;
      });
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Unknown request error.';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }

  function openSupportBooking() {
    if (!SUPPORT_BOOKING_URL) {
      return;
    }
    window.open(SUPPORT_BOOKING_URL, '_blank', 'noopener,noreferrer');
  }

  async function copyReportToClipboard() {
    if (!data || !navigator.clipboard) {
      setCopyState('error');
      return;
    }
    try {
      await navigator.clipboard.writeText(buildReportClipboardText(data));
      setCopyState('copied');
    } catch {
      setCopyState('error');
    }
  }

  const copyButtonLabel = copyState === 'copied' ? 'Copied' : copyState === 'error' ? 'Copy Failed' : 'Copy Report';
  const copyButtonTitle = !data || isLoading ? 'Load a report to copy it' : copyButtonLabel;
  const refreshButtonTitle = !data ? 'Create a report first' : isLoading ? 'Generating report...' : 'Refresh Report';
  const createButtonTitle = isLoading ? 'Creating report...' : 'Create Report';

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
          <div className="smart-header-controls">
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
            <button
              type="button"
              className={`icon-button smart-copy-report-button ${copyState === 'copied' ? 'is-copied' : ''} ${
                copyState === 'error' ? 'is-error' : ''
              }`}
              onClick={copyReportToClipboard}
              disabled={!data || isLoading}
              title={copyButtonTitle}
              aria-label={copyButtonTitle}
            >
              <svg viewBox="0 0 24 24" className="icon-16" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
            </button>
            <button
              type="button"
              className={`icon-button smart-refresh-report-button ${isLoading ? 'is-loading' : ''}`}
              onClick={() => void generateReport()}
              disabled={!data || isLoading}
              title={refreshButtonTitle}
              aria-label={refreshButtonTitle}
            >
              <svg viewBox="0 0 24 24" className="icon-16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 12a9 9 0 0 1 15.2-6.4" />
                <path d="M19 3v4h-4" />
                <path d="M21 12a9 9 0 0 1-15.2 6.4" />
                <path d="M5 21v-4h4" />
              </svg>
            </button>
            <button
              type="button"
              className="btn-primary smart-book-support-button"
              onClick={openSupportBooking}
              disabled={!SUPPORT_BOOKING_URL}
              title={!SUPPORT_BOOKING_URL ? 'Set VITE_SUPPORT_BOOKING_URL in frontend/.env' : undefined}
            >
              Book Support
            </button>
          </div>
        </div>
      </div>

      {!data ? (
        <div className="page-surface smart-create-state">
          {error ? <p className="smart-create-error">{error}</p> : null}
          <button type="button" className="btn-primary smart-create-button" onClick={() => void generateReport()} disabled={isLoading}>
            {createButtonTitle}
          </button>
        </div>
      ) : null}

      {isLoading && data ? <p className="monitoring-state">Generating updated smart insights...</p> : null}

      {data && error ? (
        <div className="monitoring-state monitoring-state-error">
          <p>{error}</p>
          <button type="button" className="btn-secondary monitoring-error-retry" onClick={() => void generateReport()} disabled={isLoading}>
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
