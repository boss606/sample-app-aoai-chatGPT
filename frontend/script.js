const chatMessages = document.getElementById('chat-messages');
const userInput = document.getElementById('user-input');
const sendButton = document.getElementById('send-button');
const suggestedPrompts = document.getElementById('suggested-prompts');
const jurisdictionSelect = document.getElementById('jurisdiction-select'); // Added this line
let typingIndicator; // To keep track of the indicator element

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    adjustInputHeight();
    updateSendButtonState();
    initializeTypingIndicator(); // Prepare indicator element
    addEventListeners();
});

// --- Event Listeners ---
function addEventListeners() {
    userInput.addEventListener('input', () => {
        adjustInputHeight();
        updateSendButtonState();
    });

    userInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });

    sendButton.addEventListener('click', sendMessage);

    suggestedPrompts.addEventListener('click', (event) => {
        if (event.target.classList.contains('prompt-btn')) {
            userInput.value = event.target.textContent;
            userInput.focus();
            adjustInputHeight();
            updateSendButtonState();
            // Optional: Immediately send the prompt
            // sendMessage();
        }
    });

    // Event delegation for copy/feedback buttons
    chatMessages.addEventListener('click', (event) => {
        const target = event.target.closest('button'); // Find the closest button clicked
        if (!target) return;

        const messageDiv = target.closest('.message.bot');
        if (!messageDiv) return;

        if (target.classList.contains('copy-btn')) {
            copyMessageToClipboard(messageDiv);
        } else if (target.classList.contains('feedback-btn')) {
            handleFeedback(target);
        }
    });
}

// --- Core Functions ---
function sendMessage() {
    const messageText = userInput.value.trim();
    const selectedJurisdiction = jurisdictionSelect.value; // Get selected jurisdiction

    if (messageText) {
        displayMessage(messageText, 'user');
        userInput.value = '';
        adjustInputHeight();
        updateSendButtonState();
        showTypingIndicator();

        // --- TODO: Send messageText AND selectedJurisdiction to your chatbot API ---
        console.log(`Sending to API (Jurisdiction: ${selectedJurisdiction}):`, messageText); // Placeholder includes jurisdiction

        // Simulate bot response WITH sources
        setTimeout(() => {
            hideTypingIndicator();
            const simulatedResponse = {
                text: `Simulated response regarding "${messageText}" under **${selectedJurisdiction}** jurisdiction. Key points are... [1]. Further considerations include... [2].`, // Example using jurisdiction
                sources: [
                    { title: `Relevant ${selectedJurisdiction} Statute Section 123`, url: "#" },
                    { title: `Related ${selectedJurisdiction} Case Law Example`, url: "#" }
                ]
            };
            displayMessage(simulatedResponse, 'bot'); // Pass the object
        }, 1500 + Math.random() * 1000); // Add some random delay
    }
}

// Enhanced displayMessage function
function displayMessage(data, type) {
    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type);
    const messageId = Date.now(); // Simple unique ID
    messageDiv.dataset.messageId = messageId;

    const contentDiv = document.createElement('div');
    contentDiv.classList.add('message-content');

    let messageText = '';
    let sources = [];

    // Check if data is an object with text and sources, or just text
    if (typeof data === 'object' && data !== null && data.text) {
        messageText = data.text;
        sources = data.sources || []; // Get sources array, default to empty
    } else if (typeof data === 'string') {
        messageText = data;
    } else {
        messageText = "Received unexpected data format."; // Fallback
    }

    // Basic markdown support + link inline citations to source list below
    contentDiv.innerHTML = messageText
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>') // Bold
        .replace(/\*(.*?)\*/g, '<em>$1</em>')       // Italics
        .replace(/\[(\d+)\]/g, `<a href="#source-$1-${messageId}" onclick="scrollToSource('source-$1-${messageId}')" class="citation-marker">[$1]</a>`); // Link to source ID


    // Add sources section if sources exist
    if (type === 'bot' && sources.length > 0) {
        const sourcesDiv = document.createElement('div');
        sourcesDiv.classList.add('message-sources');
        let sourcesListHtml = '<small>Sources:</small><ol>';
        sources.forEach((source, index) => {
            // Assume source is an object like { url: "...", title: "..." }
            const sourceNum = index + 1;
            const title = source.title || source.url || `Source ${sourceNum}`; // Use title, URL, or number as fallback
            const url = source.url || '#';
            // Add ID to list item for linking
            sourcesListHtml += `<li id="source-${sourceNum}-${messageId}"><a href="${url}" target="_blank" title="${title}"> ${title}</a></li>`; // Removed [num] from link text, added before via CSS
        });
        sourcesListHtml += '</ol>';
        sourcesDiv.innerHTML = sourcesListHtml;
        contentDiv.appendChild(sourcesDiv); // Append sources inside the content bubble
    }

    messageDiv.appendChild(contentDiv);

    // Add action buttons for bot messages
    if (type === 'bot') {
        const actionsDiv = document.createElement('div');
        actionsDiv.classList.add('message-actions');
        actionsDiv.innerHTML = `
            <button class="action-btn copy-btn" title="Copy"><i class="far fa-copy"></i></button>
            <button class="action-btn feedback-btn good" title="Good response"><i class="far fa-thumbs-up"></i></button>
            <button class="action-btn feedback-btn bad" title="Bad response"><i class="far fa-thumbs-down"></i></button>
        `;
        messageDiv.appendChild(actionsDiv);
    }

    // Insert message
    if (typingIndicator && typingIndicator.parentNode === chatMessages) {
        chatMessages.insertBefore(messageDiv, typingIndicator);
    } else {
        chatMessages.appendChild(messageDiv);
    }

    scrollToBottom(false); // Don't use smooth scroll initially if many messages load
    // Use smooth scroll after a slight delay to ensure layout is complete
    setTimeout(() => scrollToBottom(true), 50);
}


// --- Typing Indicator ---
function initializeTypingIndicator() {
    typingIndicator = document.createElement('div');
    typingIndicator.classList.add('message', 'bot', 'typing-indicator');
    typingIndicator.innerHTML = `<div class="message-content"><span>.</span><span>.</span><span>.</span></div>`;
    typingIndicator.style.display = 'none'; // Initially hidden
    chatMessages.appendChild(typingIndicator);
}

function showTypingIndicator() {
    if (typingIndicator) {
        typingIndicator.style.display = 'flex'; // Use flex for alignment
        scrollToBottom(true);
    }
}

function hideTypingIndicator() {
    if (typingIndicator) {
        typingIndicator.style.display = 'none';
    }
}

// --- Input Area Helpers ---
function adjustInputHeight() {
    userInput.style.height = 'auto'; // Reset height to recalculate scrollHeight
    let newHeight = userInput.scrollHeight;
    // Consider max-height defined in CSS
    const maxHeight = parseInt(window.getComputedStyle(userInput).maxHeight, 10);
    if (maxHeight && newHeight > maxHeight) {
        newHeight = maxHeight;
        userInput.style.overflowY = 'auto'; // Show scrollbar if max height reached
    } else {
        userInput.style.overflowY = 'hidden'; // Hide scrollbar if not needed
    }
    // Add a small buffer only if not at max height to prevent scrollbar flicker
    const buffer = (newHeight < maxHeight) ? 2 : 0;
    userInput.style.height = (newHeight + buffer) + 'px';
}


function updateSendButtonState() {
    sendButton.disabled = userInput.value.trim() === '';
}

function scrollToBottom(smooth = true) {
    // Scroll smoothly to the bottom
    chatMessages.scrollTo({
        top: chatMessages.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto'
    });
}

// Helper function to scroll to a specific source when citation is clicked
function scrollToSource(sourceId) {
    const sourceElement = document.getElementById(sourceId);
    if (sourceElement) {
        sourceElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        // Optional: Add a temporary highlight effect
        sourceElement.style.transition = 'background-color 0.5s ease';
        sourceElement.style.backgroundColor = 'rgba(13, 110, 253, 0.1)'; // Light blue highlight
        setTimeout(() => {
            sourceElement.style.backgroundColor = ''; // Remove highlight
        }, 1500);
    }
}

// --- Action Button Handlers ---
function copyMessageToClipboard(messageDiv) {
    // Try to copy only the main text, excluding sources if present
    let textToCopy = '';
    const contentChildren = Array.from(messageDiv.querySelector('.message-content').childNodes);
    contentChildren.forEach(node => {
        if (node.nodeType === Node.TEXT_NODE) {
            textToCopy += node.textContent;
        } else if (node.nodeType === Node.ELEMENT_NODE && !node.classList.contains('message-sources')) {
            // Include text from elements like <strong> or <em>, but skip the sources div
             textToCopy += node.textContent || node.innerText;
        }
    });
     textToCopy = textToCopy.trim(); // Clean up extra whitespace

    if (textToCopy) {
        navigator.clipboard.writeText(textToCopy)
            .then(() => {
                const copyBtn = messageDiv.querySelector('.copy-btn');
                if (copyBtn) {
                   const originalIcon = copyBtn.innerHTML;
                   copyBtn.innerHTML = '<i class="fas fa-check"></i>'; // Checkmark
                   copyBtn.disabled = true; // Briefly disable
                   setTimeout(() => {
                       copyBtn.innerHTML = originalIcon;
                       copyBtn.disabled = false;
                    }, 1500);
                }
            })
            .catch(err => {
                console.error('Failed to copy text: ', err);
                alert('Failed to copy text.'); // Simple error feedback
            });
    } else {
         console.warn("No text content found to copy, excluding sources.");
    }
}

function handleFeedback(button) {
    const messageId = button.closest('.message.bot').dataset.messageId;
    const feedbackType = button.classList.contains('good') ? 'good' : 'bad';
    console.log(`Feedback received: ${feedbackType} for message ${messageId}`); // Placeholder
    // --- TODO: Send feedback (messageId, feedbackType) to your backend ---

    // Visual feedback: Highlight selected, disable others
    const actionsDiv = button.closest('.message-actions');
    const feedbackButtons = actionsDiv.querySelectorAll('.feedback-btn');
    feedbackButtons.forEach(btn => {
        btn.disabled = true; // Disable all feedback buttons
        btn.style.color = '#adb5bd'; // Dim disabled buttons
    });
    // Highlight the clicked one
    button.style.color = feedbackType === 'good' ? '#198754' : '#dc3545';
}