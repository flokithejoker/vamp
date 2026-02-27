type FetchJsonWithRetryOptions = {
  init?: RequestInit;
  signal?: AbortSignal;
  retries?: number;
  retryDelaysMs?: number[];
};

const RETRYABLE_STATUS_CODES = new Set([429, 502, 503, 504]);
const DEFAULT_RETRIES = 2;
const DEFAULT_RETRY_DELAYS_MS = [200, 500];

type ParsedBody = {
  data: unknown;
  text: string;
};

function createError(message: string, status?: number, details?: unknown): Error & { status?: number; details?: unknown } {
  const error = new Error(message) as Error & { status?: number; details?: unknown };
  error.status = status;
  error.details = details;
  return error;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

async function parseResponseBody(response: Response): Promise<ParsedBody> {
  const text = await response.text();
  if (!text) {
    return { data: null, text: '' };
  }

  try {
    return { data: JSON.parse(text) as unknown, text };
  } catch {
    return { data: null, text };
  }
}

function extractMessage(parsedBody: ParsedBody, fallback: string): string {
  const { data, text } = parsedBody;
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    const record = data as Record<string, unknown>;
    const error = record.error;
    if (typeof error === 'string' && error.trim()) {
      return error.trim();
    }
    const message = record.message;
    if (typeof message === 'string' && message.trim()) {
      return message.trim();
    }
  }

  if (text.trim()) {
    return text.trim();
  }

  return fallback;
}

async function wait(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) {
    return;
  }

  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);

    function onAbort() {
      window.clearTimeout(timer);
      reject(new DOMException('The operation was aborted.', 'AbortError'));
    }

    if (signal) {
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener('abort', onAbort, { once: true });
    }
  });
}

export async function fetchJsonWithRetry<T>(
  url: string,
  options: FetchJsonWithRetryOptions = {}
): Promise<T> {
  const retries = Math.max(0, options.retries ?? DEFAULT_RETRIES);
  const maxAttempts = retries + 1;
  const retryDelaysMs = options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;

  let lastError: unknown = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        ...options.init,
        signal: options.signal,
      });

      const parsedBody = await parseResponseBody(response);
      if (response.ok) {
        return (parsedBody.data ?? ({} as T)) as T;
      }

      const message = extractMessage(parsedBody, `Request failed with status ${response.status}.`);
      const error = createError(message, response.status, parsedBody.data);

      if (RETRYABLE_STATUS_CODES.has(response.status) && attempt < maxAttempts) {
        const delay = retryDelaysMs[Math.min(attempt - 1, retryDelaysMs.length - 1)] ?? 0;
        await wait(delay, options.signal);
        continue;
      }

      throw error;
    } catch (error) {
      if (isAbortError(error)) {
        throw error;
      }

      lastError = error;
      if (attempt < maxAttempts) {
        const delay = retryDelaysMs[Math.min(attempt - 1, retryDelaysMs.length - 1)] ?? 0;
        await wait(delay, options.signal);
        continue;
      }
    }
  }

  if (lastError instanceof Error) {
    throw lastError;
  }

  throw new Error('Unexpected request error.');
}
