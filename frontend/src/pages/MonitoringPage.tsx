import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchJsonWithRetry } from '../lib/http';

type MonitoringConversationItem = {
  conversationId: string;
  title: string;
  status: 'processing' | 'done' | 'failed';
  callSuccessful: boolean | null;
  startTimeUnix: number;
  startTimeLabel: string;
  durationSeconds: number | null;
  durationLabel: string;
  costRaw: number | string | null;
  costLabel: string;
  toolNames: string[];
};

type MonitoringConversationsResponse = {
  items: MonitoringConversationItem[];
  hasMore: boolean;
  nextCursor: string | null;
};

const DEFAULT_PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;
const DISPLAY_TIMEZONE = 'Europe/Berlin';
type MonitoringOutcomeTone = 'success' | 'failed' | 'processing';

function formatMonitoringDateParts(startTimeUnix: number): { date: string; time: string } {
  if (!Number.isFinite(startTimeUnix) || startTimeUnix <= 0) {
    return { date: '-', time: '-' };
  }

  const date = new Date(startTimeUnix * 1000);

  const datePart = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    timeZone: DISPLAY_TIMEZONE,
  }).format(date);

  const timePart = new Intl.DateTimeFormat('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: DISPLAY_TIMEZONE,
  }).format(date);

  return { date: datePart, time: timePart };
}

function resolveMonitoringOutcomeTone(item: MonitoringConversationItem): MonitoringOutcomeTone {
  if (item.callSuccessful === true) {
    return 'success';
  }
  if (item.callSuccessful === false) {
    return 'failed';
  }
  if (item.status === 'failed') {
    return 'failed';
  }
  if (item.status === 'done') {
    return 'success';
  }
  return 'processing';
}

function formatMonitoringOutcomeLabel(item: MonitoringConversationItem): string {
  const tone = resolveMonitoringOutcomeTone(item);
  if (tone === 'success') {
    return 'Successful';
  }
  if (tone === 'failed') {
    return 'Failed';
  }
  return 'In progress';
}

export function MonitoringPage() {
  const [items, setItems] = useState<MonitoringConversationItem[]>([]);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const requestIdRef = useRef(0);

  async function loadConversations(options: { append: boolean; cursor?: string; searchTerm: string }) {
    const { append, cursor, searchTerm } = options;
    const requestId = ++requestIdRef.current;

    if (append) {
      setIsLoadingMore(true);
    } else {
      setIsInitialLoading(true);
    }

    try {
      const params = new URLSearchParams();
      params.set('pageSize', String(DEFAULT_PAGE_SIZE));

      if (cursor) {
        params.set('cursor', cursor);
      }
      if (searchTerm) {
        params.set('search', searchTerm);
      }

      if (requestId !== requestIdRef.current) {
        return;
      }

      const data = await fetchJsonWithRetry<MonitoringConversationsResponse>(
        `/api/monitoring/conversations?${params.toString()}`
      );
      if (requestId !== requestIdRef.current) {
        return;
      }
      setItems((previousItems) => (append ? [...previousItems, ...data.items] : data.items));
      setHasMore(data.hasMore);
      setNextCursor(data.nextCursor);
      setError(null);
    } catch (requestError) {
      if (requestId !== requestIdRef.current) {
        return;
      }
      const message = requestError instanceof Error ? requestError.message : 'Unknown request error.';
      setError(message);
    } finally {
      if (requestId !== requestIdRef.current) {
        return;
      }
      if (append) {
        setIsLoadingMore(false);
      } else {
        setIsInitialLoading(false);
      }
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
    };
  }, [search]);

  useEffect(() => {
    setItems([]);
    setError(null);
    setHasMore(false);
    setNextCursor(null);
    void loadConversations({ append: false, searchTerm: debouncedSearch });
  }, [debouncedSearch]);

  const hasItems = items.length > 0;
  const canLoadMore = hasMore && Boolean(nextCursor);
  const isSearchActive = debouncedSearch.length > 0;
  const resultCountLabel = `${items.length} conversation${items.length === 1 ? '' : 's'}`;
  const isRefreshDisabled = isInitialLoading || isLoadingMore;

  function refreshList() {
    void loadConversations({ append: false, searchTerm: debouncedSearch });
  }

  return (
    <section className="page-shell">
      <div className="page-surface page-heading home-hero">
        <h2>Conversations</h2>
      </div>

      <div className="page-surface monitoring-list-surface">
        <div className="monitoring-toolbar">
          <div className="monitoring-search-wrap">
            <input
              type="search"
              className="control monitoring-search-input"
              placeholder="Search title, id, keyword..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <button
              type="button"
              className={`icon-button monitoring-refresh-icon ${isInitialLoading ? 'is-loading' : ''}`}
              onClick={refreshList}
              disabled={isRefreshDisabled}
              aria-label="Refresh conversations"
              title="Refresh conversations"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="icon-16">
                <path d="M3 12a9 9 0 0 1 15.2-6.4" />
                <path d="M19 3v4h-4" />
                <path d="M21 12a9 9 0 0 1-15.2 6.4" />
                <path d="M5 21v-4h4" />
              </svg>
            </button>
            {search ? (
              <button
                type="button"
                className="btn-ghost monitoring-search-clear"
                onClick={() => setSearch('')}
                aria-label="Clear search"
              >
                Clear
              </button>
            ) : null}
          </div>
          <p className="monitoring-result-count">{resultCountLabel}</p>
        </div>

        {isInitialLoading && !hasItems ? <p className="monitoring-state">Loading conversations...</p> : null}

        {!isInitialLoading && error ? (
          <div className="monitoring-state monitoring-state-error">
            <p>{error}</p>
            <button type="button" className="btn-secondary monitoring-error-retry" onClick={refreshList}>
              Retry
            </button>
          </div>
        ) : null}

        {!isInitialLoading && !error && !hasItems && !isSearchActive ? (
          <p className="monitoring-state">No conversations found for the configured agent yet.</p>
        ) : null}

        {!isInitialLoading && !error && !hasItems && isSearchActive ? (
          <p className="monitoring-state">No conversations match "{debouncedSearch}".</p>
        ) : null}

        {hasItems ? (
          <div className="monitoring-scroll">
            <div className="monitoring-header-sticky">
              <div className="monitoring-grid monitoring-header-row" aria-hidden="true">
                <span>Conversation</span>
                <span>Date</span>
                <span>Time</span>
                <span>Duration</span>
                <span>Status</span>
                <span>Cost</span>
              </div>
            </div>
            <ul className="monitoring-list" aria-label="Conversation list">
              {items.map((conversation) => {
                const dateParts = formatMonitoringDateParts(conversation.startTimeUnix);
                const outcomeTone = resolveMonitoringOutcomeTone(conversation);

                return (
                  <li key={conversation.conversationId} className="monitoring-row-item">
                    <Link
                      to={`/monitoring/${conversation.conversationId}`}
                      state={{ conversationTitle: conversation.title }}
                      className="monitoring-row monitoring-grid"
                    >
                      <p className="monitoring-col monitoring-col-conversation" title={conversation.title}>
                        {conversation.title}
                      </p>
                      <p className="monitoring-col monitoring-col-date">{dateParts.date}</p>
                      <p className="monitoring-col monitoring-col-time">{dateParts.time}</p>
                      <p className="monitoring-col monitoring-col-duration">{conversation.durationLabel}</p>
                      <p className={`monitoring-col monitoring-col-status monitoring-outcome-${outcomeTone}`}>
                        {formatMonitoringOutcomeLabel(conversation)}
                      </p>
                      <p className="monitoring-col monitoring-col-cost">{conversation.costLabel}</p>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}

        {canLoadMore ? (
          <div className="monitoring-load-more">
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                void loadConversations({ append: true, cursor: nextCursor ?? undefined, searchTerm: debouncedSearch })
              }
              disabled={isLoadingMore}
            >
              {isLoadingMore ? 'Loading...' : 'Load more'}
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
