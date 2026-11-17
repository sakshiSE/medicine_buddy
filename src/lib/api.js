
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

// Check backend connection
export function checkHealth() {
    return apiRequest('/health');
}

// Search medicine and retrieve ingredient information
export function lookupMedicine(data) {
    return apiRequest('/lookup', data);
}

// Existing conversational AI chat
export function askMedicine(data) {
    return apiRequest('/chat', data);
}

// New independent LangChain Agent analysis
export function analyzeMedicine(data) {
    return apiRequest('/agent/analyze', data);
}
