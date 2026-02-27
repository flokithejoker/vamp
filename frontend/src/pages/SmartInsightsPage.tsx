import { useEffect, useMemo, useState } from 'react';
import { StatCard } from '../components/StatCard';
import { fetchJsonWithRetry } from '../lib/http';

type TimelineKey = '1d' | '7d' | '1m';
type ConfidenceLevel = 'low' | 'medium' | 'high';
type OperationalStatus = 'stable' | 'watch' | 'at_risk';
type CriterionIssue = 'human_escalation' | 'intent_identification' | 'call_cancellation' | 'none';
type LinkedCriterion = CriterionIssue;

type SmartInsightsReportResponse = {
  meta: {
    timeline: TimelineKey;
    generatedAtIso: string;
    totalCalls: number;
    dataCoveragePercent: number;
  };
  overview: {
    summary: string;
    operationalStatus: OperationalStatus;
    topOpportunity: string;
  };
  kpis: {
    resolutionRatePercent: number;
    unresolvedCalls: number;
    criteriaHealthScore: number;
    topIntent: {
      value: string;
      calls: number;
      sharePercent: number;
    };
    topFrictionPoint: {
      value: string;
      calls: number;
      sharePercent: number;
    };
  };
  criteria: {
    weights: {
      humanEscalation: 0.5;
      intentIdentification: 0.3;
      callCancellation: 0.2;
    };
    passRates: {
      humanEscalation: number;
      intentIdentification: number;
      callCancellation: number;
    };
    unknownRates: {
      humanEscalation: number;
      intentIdentification: number;
      callCancellation: number;
    };
    keyCriterionIssue: CriterionIssue;
  };
  hotspots: Array<{
    segmentType: 'hotel_location' | 'user_intent' | 'booking_stage' | 'topics';
    segmentValue: string;
    calls: number;
    unresolvedRatePercent: number;
    weightedCriteriaFailRatePercent: number;
    primaryFrictionPoint: string;
    knowledgeGapTopic: string;
    confidence: ConfidenceLevel;
  }>;
  actionQueue: Array<{
    priority: 1 | 2 | 3 | 4 | 5;
    recommendedInternalAction: string;
    targetSegment: string;
    linkedCriterion: LinkedCriterion;
    why: string;
    expectedImpact: 'low' | 'medium' | 'high';
    evidence: {
      calls: number;
      sharePercent: number;
    };
  }>;
  dataQuality: {
    missingFieldRates: Array<{
      field: string;
      missingPercent: number;
    }>;
    caveats: string[];
  };
};

type DrilldownContext = {
  source: 'actionQueue' | 'hotspots';
  segmentType: string;
  segmentValue: string;
};

const DEFAULT_TIMELINE: TimelineKey = '7d';
const TIMELINE_OPTIONS: Array<{ key: TimelineKey; label: string }> = [
  { key: '1d', label: '1d' },
  { key: '7d', label: '7d' },
  { key: '1m', label: '1m' },
];
const DRILLDOWN_STORAGE_KEY = 'smartInsightsDrilldownContext';

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

function formatToken(value: string): string {
  if (!value || value === 'unknown') {
    return 'Unknown';
  }
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function formatSegmentType(value: string): string {
  if (value === 'hotel_location') {
    return 'Hotel';
  }
  if (value === 'user_intent') {
    return 'Intent';
  }
  if (value === 'booking_stage') {
    return 'Booking Stage';
  }
  if (value === 'topics') {
    return 'Topic';
  }
  return formatToken(value);
}

function formatCriterionLabel(value: LinkedCriterion): string {
  if (value === 'none') {
    return 'None';
  }
  return formatToken(value);
}

function formatTopMetric(metric: { value: string; calls: number; sharePercent: number }): string {
  if (metric.calls <= 0) {
    return 'No signal';
  }
  return formatToken(metric.value);
}

function parseDrilldownContext(raw: string | null): DrilldownContext | null {
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as DrilldownContext;
    if (!parsed || typeof parsed !== 'object') {
      return null;
    }
    if (parsed.source !== 'actionQueue' && parsed.source !== 'hotspots') {
      return null;
    }
    if (typeof parsed.segmentType !== 'string' || typeof parsed.segmentValue !== 'string') {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function barBreakdown(passRate: number, unknownRate: number): { pass: number; fail: number; unknown: number } {
  const normalizedUnknown = Math.max(0, Math.min(100, unknownRate));
  const knownShare = 100 - normalizedUnknown;
  const normalizedPass = Math.max(0, Math.min(100, passRate));
  const passShare = (normalizedPass / 100) * knownShare;
  const failShare = Math.max(0, knownShare - passShare);
  return {
    pass: Number(passShare.toFixed(1)),
    fail: Number(failShare.toFixed(1)),
    unknown: Number(normalizedUnknown.toFixed(1)),
  };
}

export function SmartInsightsPage() {
  const [selectedTimeline, setSelectedTimeline] = useState<TimelineKey>(DEFAULT_TIMELINE);
  const [data, setData] = useState<SmartInsightsReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [drilldownContext, setDrilldownContext] = useState<DrilldownContext | null>(() =>
    parseDrilldownContext(window.sessionStorage.getItem(DRILLDOWN_STORAGE_KEY))
  );

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

  const hotspots = useMemo(() => {
    if (!data) {
      return [];
    }
    return [...data.hotspots].sort(
      (left, right) =>
        right.weightedCriteriaFailRatePercent - left.weightedCriteriaFailRatePercent ||
        right.unresolvedRatePercent - left.unresolvedRatePercent ||
        right.calls - left.calls
    );
  }, [data]);

  function refreshReport() {
    setReloadTick((previous) => previous + 1);
  }

  function storeDrilldownContext(context: DrilldownContext) {
    setDrilldownContext(context);
    window.sessionStorage.setItem(DRILLDOWN_STORAGE_KEY, JSON.stringify(context));
  }

  return (
    <section className="page-shell smart-insights-page">
      <div className="page-surface page-heading home-hero smart-header">
        <div className="smart-header-row">
          <div>
            <h2>Smart Insights</h2>
            {data ? (
              <p className="smart-generated-at">
                Generated {formatDateTime(data.meta.generatedAtIso)} • {formatInteger(data.meta.totalCalls)} calls
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
        <>
          <div className="page-surface smart-briefing-surface">
            <div className="smart-briefing-head">
              <h3>Shift Briefing</h3>
              <span className={`smart-status-chip smart-status-${data.overview.operationalStatus}`}>
                {formatToken(data.overview.operationalStatus)}
              </span>
            </div>
            <p className="smart-briefing-summary">{data.overview.summary}</p>
            <p className="smart-briefing-opportunity">
              <strong>Top opportunity:</strong> {data.overview.topOpportunity}
            </p>
          </div>

          <div className="smart-kpi-grid">
            <StatCard
              title="Resolution Rate"
              value={formatPercent(data.kpis.resolutionRatePercent)}
              description="Resolved + partially resolved over known outcomes."
            />
            <StatCard
              title="Unresolved Calls"
              value={formatInteger(data.kpis.unresolvedCalls)}
              description="Calls still unresolved or escalated."
            />
            <StatCard
              title="Criteria Health"
              value={formatPercent(data.kpis.criteriaHealthScore)}
              description="Weighted score from escalation, intent, and cancellation criteria."
            />
            <StatCard
              title="Top Intent"
              value={formatTopMetric(data.kpis.topIntent)}
              description={`${formatInteger(data.kpis.topIntent.calls)} calls · ${formatPercent(
                data.kpis.topIntent.sharePercent
              )}`}
            />
            <StatCard
              title="Top Friction"
              value={formatTopMetric(data.kpis.topFrictionPoint)}
              description={`${formatInteger(data.kpis.topFrictionPoint.calls)} calls · ${formatPercent(
                data.kpis.topFrictionPoint.sharePercent
              )}`}
            />
          </div>

          <div className="page-surface smart-criteria-surface">
            <div className="smart-section-header">
              <h3>Criteria Scoreboard</h3>
              <p>Weighted impact: escalation 0.5, intent 0.3, cancellation 0.2</p>
            </div>

            <div className="smart-criteria-grid">
              {[
                {
                  key: 'humanEscalation',
                  label: 'Human Escalation',
                  weight: data.criteria.weights.humanEscalation,
                  passRate: data.criteria.passRates.humanEscalation,
                  unknownRate: data.criteria.unknownRates.humanEscalation,
                },
                {
                  key: 'intentIdentification',
                  label: 'Intent Identification',
                  weight: data.criteria.weights.intentIdentification,
                  passRate: data.criteria.passRates.intentIdentification,
                  unknownRate: data.criteria.unknownRates.intentIdentification,
                },
                {
                  key: 'callCancellation',
                  label: 'Call Cancellation',
                  weight: data.criteria.weights.callCancellation,
                  passRate: data.criteria.passRates.callCancellation,
                  unknownRate: data.criteria.unknownRates.callCancellation,
                },
              ].map((criterion) => {
                const breakdown = barBreakdown(criterion.passRate, criterion.unknownRate);
                return (
                  <article key={criterion.key} className="smart-criterion-card">
                    <div className="smart-criterion-head">
                      <h4>{criterion.label}</h4>
                      <span className="smart-weight-badge">w={criterion.weight}</span>
                    </div>
                    <div className="smart-criterion-bar" aria-hidden="true">
                      <span className="smart-criterion-pass" style={{ width: `${breakdown.pass}%` }} />
                      <span className="smart-criterion-fail" style={{ width: `${breakdown.fail}%` }} />
                      <span className="smart-criterion-unknown" style={{ width: `${breakdown.unknown}%` }} />
                    </div>
                    <p className="smart-criterion-meta">
                      Pass {formatPercent(criterion.passRate)} · Unknown {formatPercent(criterion.unknownRate)}
                    </p>
                  </article>
                );
              })}
            </div>
          </div>

          <div className="page-surface smart-actions-surface">
            <div className="smart-section-header">
              <h3>Priority Action Queue</h3>
              <p>Ranked by impact x frequency from the selected timeline.</p>
            </div>
            <ol className="smart-action-list">
              {data.actionQueue.map((action) => (
                <li key={`${action.priority}-${action.recommendedInternalAction}`}>
                  <button
                    type="button"
                    className="smart-action-item"
                    onClick={() =>
                      storeDrilldownContext({
                        source: 'actionQueue',
                        segmentType: action.targetSegment.split(':')[0] || 'general',
                        segmentValue: action.targetSegment.split(':')[1] || action.targetSegment,
                      })
                    }
                  >
                    <div className="smart-action-main">
                      <span className="smart-priority-badge">#{action.priority}</span>
                      <div>
                        <p className="smart-action-title">{formatToken(action.recommendedInternalAction)}</p>
                        <p className="smart-action-why">{action.why}</p>
                      </div>
                    </div>
                    <div className="smart-action-meta">
                      <span>{action.targetSegment}</span>
                      <span>Criterion: {formatCriterionLabel(action.linkedCriterion)}</span>
                      <span className={`smart-impact-badge smart-impact-${action.expectedImpact}`}>
                        Impact {formatToken(action.expectedImpact)}
                      </span>
                      <span>
                        Evidence: {formatInteger(action.evidence.calls)} calls ({formatPercent(action.evidence.sharePercent)})
                      </span>
                    </div>
                  </button>
                </li>
              ))}
            </ol>
          </div>

          <div className="page-surface smart-hotspots-surface">
            <div className="smart-section-header">
              <h3>Hotspots</h3>
              <p>Sorted by weighted criteria fail rate.</p>
            </div>
            <div className="smart-hotspots-table-wrap">
              <table className="smart-hotspots-table">
                <thead>
                  <tr>
                    <th>Segment</th>
                    <th>Calls</th>
                    <th>Unresolved %</th>
                    <th>Criteria Fail %</th>
                    <th>Friction</th>
                    <th>Knowledge Gap</th>
                    <th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {hotspots.map((hotspot) => (
                    <tr key={`${hotspot.segmentType}:${hotspot.segmentValue}`}>
                      <td>
                        <button
                          type="button"
                          className="smart-segment-link"
                          onClick={() =>
                            storeDrilldownContext({
                              source: 'hotspots',
                              segmentType: hotspot.segmentType,
                              segmentValue: hotspot.segmentValue,
                            })
                          }
                        >
                          {formatSegmentType(hotspot.segmentType)}: {formatToken(hotspot.segmentValue)}
                        </button>
                      </td>
                      <td>{formatInteger(hotspot.calls)}</td>
                      <td>{formatPercent(hotspot.unresolvedRatePercent)}</td>
                      <td>{formatPercent(hotspot.weightedCriteriaFailRatePercent)}</td>
                      <td>{formatToken(hotspot.primaryFrictionPoint)}</td>
                      <td>{formatToken(hotspot.knowledgeGapTopic)}</td>
                      <td>
                        <span className={`smart-confidence-badge smart-confidence-${hotspot.confidence}`}>
                          {formatToken(hotspot.confidence)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {drilldownContext ? (
            <div className="page-surface smart-drilldown-banner">
              <p>
                Active drilldown context: {formatSegmentType(drilldownContext.segmentType)} ={' '}
                {formatToken(drilldownContext.segmentValue)} ({drilldownContext.source})
              </p>
            </div>
          ) : null}

          <div className="page-surface smart-data-quality-surface">
            <div className="smart-section-header">
              <h3>Data Quality</h3>
              <p>{formatPercent(data.meta.dataCoveragePercent)} overall field coverage.</p>
            </div>
            <div className="smart-missing-field-grid">
              {data.dataQuality.missingFieldRates.map((item) => (
                <span key={item.field} className="smart-missing-chip">
                  {formatToken(item.field)} missing {formatPercent(item.missingPercent)}
                </span>
              ))}
            </div>
            {data.dataQuality.caveats.length > 0 ? (
              <ul className="smart-caveats-list">
                {data.dataQuality.caveats.map((caveat) => (
                  <li key={caveat}>{caveat}</li>
                ))}
              </ul>
            ) : null}
          </div>
        </>
      ) : null}
    </section>
  );
}
