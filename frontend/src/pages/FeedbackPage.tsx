import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchJsonWithRetry } from '../lib/http';

type MonitoringConversationStatus = 'processing' | 'done' | 'failed';

type FeedbackItem = {
  callId: string;
  rating: number | null;
  comment: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  ratingUpdatedAt: string | null;
  commentUpdatedAt: string | null;
};

type FeedbackResponse = {
  items: FeedbackItem[];
};

type MonitoringConversationsSearchResponse = {
  items: Array<{
    conversationId: string;
    title: string;
    status: MonitoringConversationStatus;
    callSuccessful: boolean | null;
  }>;
};

type FeedbackTableItem = FeedbackItem & {
  conversationTitle: string;
  outcomeLabel: string;
  outcomeTone: 'success' | 'failed' | 'processing';
};

function formatFeedbackTimestamp(rawValue: string | null): string {
  if (!rawValue) {
    return '-';
  }

  const parsedDate = new Date(rawValue);
  if (Number.isNaN(parsedDate.getTime())) {
    return rawValue;
  }

  return new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsedDate);
}

function fallbackConversationTitle(callId: string): string {
  const trimmedCallId = callId.trim();
  if (!trimmedCallId) {
    return 'Conversation';
  }
  return `Conversation ${trimmedCallId.slice(0, 8)}`;
}

function resolveOutcomeTone(status: MonitoringConversationStatus, callSuccessful: boolean | null): 'success' | 'failed' | 'processing' {
  if (callSuccessful === true) {
    return 'success';
  }
  if (callSuccessful === false) {
    return 'failed';
  }
  if (status === 'failed') {
    return 'failed';
  }
  if (status === 'done') {
    return 'success';
  }
  return 'processing';
}

function formatOutcomeLabel(outcomeTone: 'success' | 'failed' | 'processing'): string {
  if (outcomeTone === 'success') {
    return 'Successful';
  }
  if (outcomeTone === 'failed') {
    return 'Failed';
  }
  return 'In progress';
}

export function FeedbackPage() {
  const [items, setItems] = useState<FeedbackTableItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function loadFeedback() {
      setIsLoading(true);
      setError(null);

      try {
        const response = await fetchJsonWithRetry<FeedbackResponse>('/api/feedback/calls?limit=100', {
          signal: controller.signal,
        });
        if (cancelled) {
          return;
        }

        const feedbackItems = response.items ?? [];
        const enrichedItems = await Promise.all(
          feedbackItems.map(async (item): Promise<FeedbackTableItem> => {
            const fallbackItem: FeedbackTableItem = {
              ...item,
              conversationTitle: fallbackConversationTitle(item.callId),
              outcomeLabel: '-',
              outcomeTone: 'processing',
            };

            try {
              const params = new URLSearchParams();
              params.set('search', item.callId);
              params.set('pageSize', '20');

              const searchResponse = await fetchJsonWithRetry<MonitoringConversationsSearchResponse>(
                `/api/monitoring/conversations?${params.toString()}`,
                { signal: controller.signal }
              );

              const matchedConversation =
                searchResponse.items.find((conversation) => conversation.conversationId === item.callId) ??
                searchResponse.items[0];
              if (!matchedConversation) {
                return fallbackItem;
              }

              const outcomeTone = resolveOutcomeTone(
                matchedConversation.status,
                matchedConversation.callSuccessful
              );
              return {
                ...item,
                conversationTitle:
                  typeof matchedConversation.title === 'string' && matchedConversation.title.trim()
                    ? matchedConversation.title.trim()
                    : fallbackConversationTitle(item.callId),
                outcomeTone,
                outcomeLabel: formatOutcomeLabel(outcomeTone),
              };
            } catch {
              return fallbackItem;
            }
          })
        );

        if (cancelled) {
          return;
        }
        setItems(enrichedItems);
      } catch (requestError) {
        if (cancelled) {
          return;
        }
        const message = requestError instanceof Error ? requestError.message : 'Unknown request error.';
        setItems([]);
        setError(message);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadFeedback();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [reloadTick]);

  return (
    <section className="page-shell feedback-page">
      <div className="page-surface page-heading home-hero">
        <h2>Feedback</h2>
      </div>

      <div className="page-surface feedback-list-surface">
        {isLoading ? <p className="monitoring-state">Loading feedback...</p> : null}

        {!isLoading && error ? (
          <div className="monitoring-state monitoring-state-error">
            <p>{error}</p>
            <button
              type="button"
              className="btn-secondary monitoring-error-retry"
              onClick={() => setReloadTick((previous) => previous + 1)}
            >
              Retry
            </button>
          </div>
        ) : null}

        {!isLoading && !error && items.length === 0 ? (
          <p className="monitoring-state">No customer feedback recorded yet.</p>
        ) : null}

        {!isLoading && !error && items.length > 0 ? (
          <div className="feedback-scroll">
            <div className="feedback-grid feedback-header-row" aria-hidden="true">
              <span className="feedback-col-conversation">Conversation</span>
              <span className="feedback-col-call-id">Call ID</span>
              <span className="feedback-col-outcome">Outcome</span>
              <span className="feedback-col-rating">Rating</span>
              <span className="feedback-col-comment">Comment</span>
              <span className="feedback-col-updated">Updated</span>
            </div>
            <ul className="feedback-list" aria-label="Customer feedback list">
              {items.map((item) => (
                <li key={item.callId} className="feedback-row">
                  <Link
                    to={`/monitoring/${item.callId}`}
                    state={{ conversationTitle: item.conversationTitle, returnTo: '/feedback' }}
                    className="feedback-grid feedback-row-link"
                  >
                    <span className="feedback-conversation">{item.conversationTitle}</span>
                    <span className="feedback-call-id">{item.callId}</span>
                    <span className={`feedback-outcome feedback-outcome-${item.outcomeTone}`}>{item.outcomeLabel}</span>
                    <span className="feedback-rating">{item.rating !== null ? `${item.rating}/5` : '-'}</span>
                    <span className="feedback-comment">{item.comment?.trim() || '-'}</span>
                    <span className="feedback-updated">{formatFeedbackTimestamp(item.updatedAt)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}
