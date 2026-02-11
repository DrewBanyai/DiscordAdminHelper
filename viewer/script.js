const API_BASE = "http://localhost:8000";

document.getElementById('search-btn').addEventListener('click', searchMessages);
document.getElementById('refresh-stats').addEventListener('click', () => fetchStats(currentTimeframe));

let currentTimeframe = 'all';

// Tab Switching Logic
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;

        // Update Buttons
        document.querySelectorAll('.tab-nav .tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        // Update Content
        document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
        document.getElementById(`${tab}-tab`).style.display = 'block';

        // Update Controls
        document.getElementById('messages-controls').style.display = tab === 'messages' ? 'flex' : 'none';
        document.getElementById('stats-controls').style.display = tab === 'stats' ? 'flex' : 'none';

        if (tab === 'stats') fetchStats(currentTimeframe);
    });
});

// Timeframe Filter Logic
document.querySelectorAll('.time-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        currentTimeframe = btn.dataset.time;
        document.querySelectorAll('.timeframe-btns .time-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        fetchStats(currentTimeframe);
    });
});

async function searchMessages() {
    const keyword = document.getElementById('keyword').value;
    const username = document.getElementById('username').value;
    const list = document.getElementById('messages-list');

    // Auto-switch back to search panel if in context view
    document.getElementById('search-results-panel').style.display = 'flex';
    document.getElementById('context-panel').style.display = 'none';

    list.innerHTML = '<p class="placeholder">Loading...</p>';

    let url = `${API_BASE}/messages?`;
    if (keyword) url += `keyword=${encodeURIComponent(keyword)}&`;
    if (username) url += `username=${encodeURIComponent(username)}&`;

    try {
        const response = await fetch(url);
        const messages = await response.json();

        if (messages.length === 0) {
            list.innerHTML = '<p class="placeholder">No messages found.</p>';
            return;
        }

        list.innerHTML = messages.map(msg => renderMessage(msg)).join('');
    } catch (error) {
        list.innerHTML = `<p class="placeholder" style="color: #ff4747">Error fetching data. Is the backend running?</p>`;
    }
}

function renderMessage(msg, isTarget = false) {
    const flagClass = msg.flag === 'green' ? 'flag-green' : (msg.flag === 'red' ? 'flag-red' : '');
    const flagIcon = msg.flag === 'green' ? '🟢' : (msg.flag === 'red' ? '🔴' : '⚪');

    return `
        <div class="message-item ${isTarget ? 'target-message' : ''} ${flagClass}" id="msg-${msg.id}">
            <div class="message-header">
                <span class="author">${escapeHtml(msg.author_name)}</span>
                <span class="flag-indicator">${getFlagIcon(msg.flag)}</span>
                <span class="timestamp">${formatDate(msg.timestamp)}</span>
            </div>
            <div class="content">${escapeHtml(msg.content)}</div>
            ${msg.attachment_urls && msg.attachment_urls.length > 0 ?
            `<div class="attachments">
                    ${msg.attachment_urls.map(url => `<img src="${url}" class="msg-img" loading="lazy" onclick="window.open('${url}', '_blank')">`).join('')}
                </div>` : ''}
            
            <div class="flag-controls">
                <button class="flag-btn green" onclick="toggleFlag('${msg.id}', 'green')">Green</button>
                <button class="flag-btn red" onclick="toggleFlag('${msg.id}', 'red')">Red</button>
                <button class="flag-btn react" onclick="promptReact('${msg.id}')">React</button>
                <button class="flag-btn none" onclick="toggleFlag('${msg.id}', 'none')">Clear</button>
            </div>
            <button class="context-btn" onclick="viewContext('${msg.id}')">View Context</button>
        </div>
    `;
}

function getFlagIcon(flag) {
    if (flag === 'green') return '🟢';
    if (flag === 'red') return '🔴';
    if (flag && flag.startsWith('pending_react:')) {
        const emoji = flag.split(':')[1];
        return `<span title="Pending reaction: ${emoji}">⏳${emoji}</span>`;
    }
    return '⚪';
}

let currentMessageIdForReact = null;

async function promptReact(messageId) {
    currentMessageIdForReact = messageId;
    const modal = document.getElementById('reaction-modal');
    const list = document.getElementById('emoji-list');
    const subtitle = modal.querySelector('.modal-subtitle');

    modal.style.display = 'block';
    list.innerHTML = '<p class="placeholder">Fetching reactions...</p>';
    subtitle.innerText = 'Fetching current reactions from Discord...';

    try {
        const response = await fetch(`${API_BASE}/messages/${messageId}/reactions`);
        const reactions = await response.json();

        if (reactions.error) {
            list.innerHTML = `<p class="placeholder" style="color: #ff4747">Error: ${reactions.error}</p>`;
            subtitle.innerText = 'Failed to fetch reactions.';
            return;
        }

        if (reactions.length === 0) {
            list.innerHTML = '<p class="placeholder">No reactions found on this post.</p>';
            subtitle.innerText = 'This post has no reactions yet.';
        } else {
            subtitle.innerText = `Select one of the ${reactions.length} reactions found:`;
            list.innerHTML = reactions.map(r => `
                <div class="emoji-chip" onclick="selectEmoji('${r.emoji_str}')">
                    <div class="emoji-visual">${r.id ? `<img src="https://cdn.discordapp.com/emojis/${r.id}.png?size=48" style="width:32px;height:32px;">` : r.name}</div>
                    <div class="emoji-count">${r.count} users</div>
                </div>
            `).join('');
        }
    } catch (error) {
        list.innerHTML = `<p class="placeholder" style="color: #ff4747">Network error.</p>`;
    }
}

function closeModal() {
    document.getElementById('reaction-modal').style.display = 'none';
    currentMessageIdForReact = null;
}

async function selectEmoji(emojiStr) {
    if (currentMessageIdForReact) {
        await toggleFlag(currentMessageIdForReact, `pending_react:${emojiStr}`);
        closeModal();
    }
}

async function customReact() {
    const emoji = prompt("Enter the emoji you want the bot to react with (e.g. ✅, 👍):");
    if (emoji && emoji.trim()) {
        await selectEmoji(emoji.trim());
    }
}

window.onclick = function (event) {
    const modal = document.getElementById('reaction-modal');
    if (event.target == modal) {
        closeModal();
    }
}

async function toggleFlag(messageId, color) {
    try {
        const response = await fetch(`${API_BASE}/messages/${messageId}/flag`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ flag: color })
        });

        if (response.ok) {
            // Find all instances of this message in the UI and update them
            const msgElements = document.querySelectorAll(`[id="msg-${messageId}"]`);
            msgElements.forEach(el => {
                el.classList.remove('flag-green', 'flag-red', 'flag-pending_react');
                if (color !== 'none') el.classList.add(`flag-${color.split(':')[0]}`);

                const icon = el.querySelector('.flag-indicator');
                if (icon) icon.innerHTML = getFlagIcon(color);
            });
        }
    } catch (error) {
        console.error("Failed to update flag:", error);
    }
}

async function viewContext(messageId) {
    const searchPanel = document.getElementById('search-results-panel');
    const contextPanel = document.getElementById('context-panel');
    const contextList = document.getElementById('context-list');

    searchPanel.style.display = 'none';
    contextPanel.style.display = 'flex';
    contextList.innerHTML = '<p class="placeholder">Loading context...</p>';

    try {
        const response = await fetch(`${API_BASE}/messages/${messageId}/context`);
        const messages = await response.json();

        if (messages.error) {
            contextList.innerHTML = `<p class="placeholder">${messages.error}</p>`;
            return;
        }

        contextList.innerHTML = messages.map(msg => renderMessage(msg, msg.id === messageId)).join('');

        // Scroll target message into view
        setTimeout(() => {
            const target = contextList.querySelector('.target-message');
            if (target) {
                target.scrollIntoView({ behavior: 'auto', block: 'center' });
            }
        }, 50);

    } catch (error) {
        contextList.innerHTML = `<p class="placeholder" style="color: #ff4747">Error fetching context.</p>`;
    }
}

document.getElementById('context-back-btn').addEventListener('click', () => {
    document.getElementById('search-results-panel').style.display = 'flex';
    document.getElementById('context-panel').style.display = 'none';
});

async function fetchStats(timeframe = 'all') {
    const container = document.getElementById('frequency-table');
    container.innerHTML = '<p class="placeholder">Loading stats...</p>';

    try {
        const response = await fetch(`${API_BASE}/stats/word-frequency?timeframe=${timeframe}`);
        const stats = await response.json();

        if (stats.length === 0) {
            container.innerHTML = '<p class="placeholder">Not enough data to generate stats.</p>';
            return;
        }

        const maxCount = Math.max(...stats.map(s => s.count));

        container.innerHTML = stats.map(s => `
            <div class="freq-item">
                <span class="word">${escapeHtml(s.word)}</span>
                <div class="bar-container">
                    <div class="bar" style="width: ${(s.count / maxCount) * 100}%"></div>
                </div>
                <span class="count">${s.count}</span>
            </div>
        `).join('');
    } catch (error) {
        container.innerHTML = `<p class="placeholder" style="color: #ff4747">Error fetching stats.</p>`;
    }
}

function formatDate(isoStr) {
    const date = new Date(isoStr);
    return date.toLocaleString();
}

function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Initial load
fetchStats();
