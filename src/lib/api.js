
const REQUEST_TIMEOUT_MS = 60000;

async function request(path, body) {
  const controller = new AbortController();
  const timeoutId = setTimeout(
    () => controller.abort(),
    REQUEST_TIMEOUT_MS
  );

  try {
    const response = await fetch(`/api${path}`, {
      method: body ? 'POST' : 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal
    });

    const responseText = await response.text();

    let data = null;

    if (responseText.trim()) {
      try {
        data = JSON.parse(responseText);
      } catch {
        throw new Error(
          'The server returned an unreadable response. Please check the backend and try again.'
        );
      }
    }

    if (!response.ok) {
      const detail = data?.detail;
      let message = 'The request could not be completed.';

      if (typeof detail === 'string') {
        message = detail;
      } else if (Array.isArray(detail)) {
        message = detail
          .map((item) => item.msg)
          .filter(Boolean)
          .join('; ') || message;
      } else if (detail && typeof detail === 'object') {
        message = detail.message || message;
      }

      throw new Error(message);
    }

    if (data === null) {
      throw new Error('The server returned an empty response. Please try again.');
    }

    return data;

  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(
        'The request took too long. Please check your connection and try again.'
      );
    }

    if (error instanceof TypeError) {
      throw new Error(
        'Could not connect to the server. Please check whether the backend is running.'
      );
    }

    throw error;

  } finally {
    clearTimeout(timeoutId);
  }
}

export const checkHealth = () => request('/health');
export const lookupMedicine = (payload) => request('/lookup', payload);
export const askMedicine = (payload) => request('/chat', payload);
