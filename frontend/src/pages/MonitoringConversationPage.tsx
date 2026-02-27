import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { WaveformAudioPlayer } from '../components/WaveformAudioPlayer';
import { fetchJsonWithRetry } from '../lib/http';

type MonitoringToolEvent = {
  id: string;
  kind: 'call' | 'result';
  name: string;
  payload: unknown;
};

type MonitoringTranscriptTurn = {
  id: string;
  role: 'user' | 'agent' | 'system' | 'tool';
  message: string;
  timeInCallSeconds: number | null;
  timeLabel: string;
  toolEvents: MonitoringToolEvent[];
};

type MonitoringToolUsed = {
  name: string;
  count: number;
};

type MonitoringConversationDetailItem = {
  conversationId: string;
  title: string;
  status: 'processing' | 'done' | 'failed';
  startTimeUnix: number;
  startTimeLabel: string;
  durationSeconds: number | null;
  durationLabel: string;
  costRaw: number | string | null;
  costLabel: string;
  summary: string;
  callSuccessful: boolean | null;
  hasAudio: boolean;
  hasUserAudio: boolean;
  hasResponseAudio: boolean;
  transcript: MonitoringTranscriptTurn[];
  toolsUsed: MonitoringToolUsed[];
};

type MonitoringConversationDetailResponse = {
  item: MonitoringConversationDetailItem;
  raw: Record<string, unknown>;
};

type ConversationLocationState = {
  conversationTitle?: string;
};

function readPath(root: unknown, path: string): unknown {
  let current: unknown = root;
  const parts = path.split('.');

  for (const part of parts) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) {
      return null;
    }
    current = (current as Record<string, unknown>)[part];
  }

  return current ?? null;
}

function formatStatusLabel(status: MonitoringConversationDetailItem['status']): string {
  if (status === 'done') {
    return 'Done';
  }
  if (status === 'failed') {
    return 'Failed';
  }
  return 'Processing';
}

function formatRoleLabel(role: MonitoringTranscriptTurn['role']): string {
  if (role === 'user') {
    return 'Customer';
  }
  if (role === 'agent') {
    return 'Viktoria';
  }
  if (role === 'tool') {
    return 'Tool';
  }
  return 'System';
}

function formatOutcome(item: MonitoringConversationDetailItem): string {
  if (item.callSuccessful === true) {
    return 'Successful';
  }
  if (item.callSuccessful === false) {
    return 'Failed';
  }
  if (item.status === 'failed') {
    return 'Failed';
  }
  if (item.status === 'done') {
    return 'Successful';
  }
  if (item.status === 'processing') {
    return 'In progress';
  }
  return 'In progress';
}

function serializePayload(value: unknown): string {
  if (value === null || value === undefined) {
    return '-';
  }
  if (typeof value === 'string') {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function MonitoringConversationPage() {
  const { conversationId } = useParams();
  const location = useLocation();
  const locationState = location.state as ConversationLocationState | null;
  const routeConversationTitle =
    typeof locationState?.conversationTitle === 'string' && locationState.conversationTitle.trim()
      ? locationState.conversationTitle.trim()
      : null;

  const [item, setItem] = useState<MonitoringConversationDetailItem | null>(null);
  const [raw, setRaw] = useState<Record<string, unknown> | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [isDetailsOpen, setIsDetailsOpen] = useState(true);
  const [expandedToolKeys, setExpandedToolKeys] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!conversationId) {
      setItem(null);
      setRaw(null);
      setError('Conversation id is missing.');
      setIsLoading(false);
      return;
    }

    const selectedConversationId = conversationId;
    const controller = new AbortController();
    let cancelled = false;

    async function loadConversationDetail() {
      setIsLoading(true);
      setError(null);
      setExpandedToolKeys({});

      try {
        const data = await fetchJsonWithRetry<MonitoringConversationDetailResponse>(
          `/api/monitoring/conversations/${encodeURIComponent(selectedConversationId)}`,
          { signal: controller.signal }
        );

        if (cancelled) {
          return;
        }

        setItem(data.item);
        setRaw(data.raw);
      } catch (requestError) {
        if (cancelled) {
          return;
        }
        const message = requestError instanceof Error ? requestError.message : 'Unknown request error.';
        setItem(null);
        setRaw(null);
        setError(message);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadConversationDetail();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [conversationId, reloadTick]);

  const advancedDetails = useMemo(
    () => ({
      evaluationCriteriaResults: readPath(raw, 'analysis.evaluation_criteria_results'),
      dataCollectionResults: readPath(raw, 'analysis.data_collection_results'),
      feedback: readPath(raw, 'metadata.feedback'),
      terminationReason: readPath(raw, 'metadata.termination_reason'),
      callEndReason: readPath(raw, 'metadata.call_end_reason'),
    }),
    [raw]
  );

  const displayTitle = useMemo(() => {
    if (routeConversationTitle) {
      return routeConversationTitle;
    }
    if (item) {
      return item.title;
    }
    return 'Conversation';
  }, [item, routeConversationTitle]);

  function toggleToolPayload(toolKey: string) {
    setExpandedToolKeys((previousState) => ({
      ...previousState,
      [toolKey]: !previousState[toolKey],
    }));
  }

  function retryLoad() {
    setReloadTick((previous) => previous + 1);
  }

  const detailsToggleLabel = isDetailsOpen ? 'Hide details' : 'Show details';

  return (
    <section className="page-shell monitoring-conversation-page">
      <div className="page-surface page-heading home-hero">
        <div className="monitoring-conversation-hero">
          <div className="monitoring-conversation-header-row">
            <div className="monitoring-conversation-title-group">
              <Link
                to="/monitoring"
                className="icon-button monitoring-back-icon"
                aria-label="Back to conversations"
                title="Back to conversations"
              >
                <svg viewBox="0 0 24 24" className="icon-16" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M15 18l-6-6 6-6" />
                </svg>
              </Link>
              <h2 title={displayTitle}>{displayTitle}</h2>
            </div>

            {item ? (
              <button
                type="button"
                className="btn-ghost monitoring-details-inline-toggle"
                onClick={() => setIsDetailsOpen((previousState) => !previousState)}
                aria-expanded={isDetailsOpen}
              >
                <svg viewBox="0 0 24 24" className="icon-16" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 6h16" />
                  <path d="M4 12h16" />
                  <path d="M4 18h10" />
                </svg>
                <span>{detailsToggleLabel}</span>
              </button>
            ) : null}
          </div>

          {item ? (
            <div className="monitoring-conversation-badges">
              <span className={`monitoring-status-chip monitoring-status-${item.status}`}>
                {formatStatusLabel(item.status)}
              </span>
              <span className="monitoring-subtle-chip">{item.startTimeLabel}</span>
            </div>
          ) : null}
        </div>
      </div>

      {isLoading ? <p className="monitoring-state">Loading conversation...</p> : null}
      {!isLoading && error ? (
        <div className="monitoring-state monitoring-state-error">
          <p>{error}</p>
          <button type="button" className="btn-secondary monitoring-error-retry" onClick={retryLoad}>
            Retry
          </button>
        </div>
      ) : null}

      {!isLoading && !error && item ? (
        <>
          <div className="page-surface monitoring-audio-surface">
            {item.hasAudio ? (
              <WaveformAudioPlayer
                audioUrl={`/api/monitoring/conversations/${encodeURIComponent(item.conversationId)}/audio`}
              />
            ) : (
              <p className="monitoring-audio-empty">No recording available for this conversation.</p>
            )}
          </div>

          <div className={`monitoring-conversation-main ${isDetailsOpen ? 'is-details-open' : 'is-details-collapsed'}`}>
            <div className="page-surface monitoring-transcript-surface">
              {item.transcript.length === 0 ? (
                <p className="monitoring-state">No transcript is available for this conversation yet.</p>
              ) : (
                <ul className="monitoring-transcript-list" aria-label="Conversation transcript">
                  {item.transcript.map((turn) => (
                    <li key={turn.id} className={`monitoring-turn monitoring-turn-${turn.role}`}>
                      <div className="monitoring-turn-bubble">
                        <div className="monitoring-turn-meta">
                          <span>{formatRoleLabel(turn.role)}</span>
                          <span>{turn.timeLabel}</span>
                        </div>
                        <p className="monitoring-turn-message">{turn.message}</p>

                        {turn.toolEvents.length > 0 ? (
                          <div className="monitoring-tool-section">
                            <div className="monitoring-tool-badges">
                              {turn.toolEvents.map((toolEvent) => {
                                const toolKey = `${turn.id}:${toolEvent.id}`;
                                const isExpanded = Boolean(expandedToolKeys[toolKey]);

                                return (
                                  <button
                                    type="button"
                                    key={toolKey}
                                    className={`monitoring-tool-badge ${isExpanded ? 'is-active' : ''}`}
                                    onClick={() => toggleToolPayload(toolKey)}
                                  >
                                    {toolEvent.kind === 'call' ? 'Call' : 'Result'}: {toolEvent.name}
                                  </button>
                                );
                              })}
                            </div>

                            {turn.toolEvents.map((toolEvent) => {
                              const toolKey = `${turn.id}:${toolEvent.id}`;
                              if (!expandedToolKeys[toolKey]) {
                                return null;
                              }

                              return (
                                <pre key={`${toolKey}-payload`} className="monitoring-tool-payload">
                                  {serializePayload(toolEvent.payload)}
                                </pre>
                              );
                            })}
                          </div>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {isDetailsOpen ? (
              <aside className="page-surface monitoring-details-panel" aria-label="Conversation details">
                <div className="monitoring-details-grid">
                  <div className="monitoring-details-item">
                    <span>Duration</span>
                    <strong>{item.durationLabel}</strong>
                  </div>
                  <div className="monitoring-details-item">
                    <span>Cost</span>
                    <strong>{item.costLabel}</strong>
                  </div>
                  <div className="monitoring-details-item">
                    <span>Outcome</span>
                    <strong>{formatOutcome(item)}</strong>
                  </div>
                  <div className="monitoring-details-item">
                    <span>Status</span>
                    <strong>{formatStatusLabel(item.status)}</strong>
                  </div>
                </div>

                <div className="monitoring-details-block">
                  <h3>Summary</h3>
                  <p>{item.summary || '-'}</p>
                </div>

                <div className="monitoring-details-block">
                  <h3>Tools Used</h3>
                  {item.toolsUsed.length === 0 ? (
                    <p className="monitoring-details-empty">No tool usage recorded.</p>
                  ) : (
                    <ul className="monitoring-tools-used-list">
                      {item.toolsUsed.map((tool) => (
                        <li key={tool.name}>
                          <span>{tool.name}</span>
                          <strong>{tool.count}</strong>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <details className="monitoring-advanced-details">
                  <summary>Advanced</summary>
                  <pre>{serializePayload(advancedDetails)}</pre>
                </details>
              </aside>
            ) : null}
          </div>

          {isDetailsOpen ? (
            <button
              type="button"
              className="monitoring-details-backdrop"
              onClick={() => setIsDetailsOpen(false)}
              aria-label="Close details panel"
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}
