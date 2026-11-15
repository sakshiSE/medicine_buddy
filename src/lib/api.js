
const API_URL = '/api';

async function apiRequest(path, data = null) {
    const options = {
        method: data ? 'POST' : 'GET',
        headers: {
            'Content-Type': 'application/json'
        }
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(`${API_URL}${path}`, options);

    if (!response.ok) {
        const message = await response.text();
        throw new Error(message || `API error: ${response.status}`);
    }

    return response.json();
}

export function checkHealth() {
    return apiRequest('/health');
}

export function lookupMedicine(data) {
    return apiRequest('/lookup', data);
}

export function askMedicine(data) {
    return apiRequest('/chat', data);
}
