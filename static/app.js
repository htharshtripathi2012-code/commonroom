"use strict";

const feeds = [...document.querySelectorAll(".message-feed[data-kind][data-id]")];
const liveStatus = document.querySelector(".live-indicator");

function appendMessage(feed, message) {
  feed.querySelector(".empty-feed")?.remove();
  const item = document.createElement("li");
  item.className = "message";
  item.dataset.messageId = String(message.id);

  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = message.author.slice(0, 1).toUpperCase();

  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const author = document.createElement("strong");
  author.textContent = message.author;
  const time = document.createElement("time");
  time.dateTime = message.created_at;
  time.textContent = message.created_at.replace("T", " ").slice(0, 16);
  const body = document.createElement("p");
  body.textContent = message.body;
  meta.append(author, time);
  content.append(meta, body);

  if (!feed.dataset.parent && feed.dataset.team) {
    const params = new URLSearchParams({ team: feed.dataset.team, thread: message.id });
    params.set(feed.dataset.kind, feed.dataset.id);
    const link = document.createElement("a");
    link.className = "thread-link";
    link.href = `/?${params}`;
    link.textContent = "Reply in thread";
    content.append(link);
  }
  item.append(avatar, content);
  feed.append(item);
}

async function updateFeed(feed) {
  const params = new URLSearchParams({
    kind: feed.dataset.kind,
    id: feed.dataset.id,
    after: feed.dataset.after || "0",
  });
  if (feed.dataset.parent) params.set("parent", feed.dataset.parent);
  const response = await fetch(`/api/messages?${params}`, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("Chat connection unavailable");
  const payload = await response.json();
  for (const message of payload.messages) {
    appendMessage(feed, message);
    feed.dataset.after = String(message.id);
  }
  if (payload.messages.length) feed.scrollTop = feed.scrollHeight;
}

async function refreshMessages() {
  if (document.hidden || feeds.length === 0) return;
  try {
    await Promise.all(feeds.map(updateFeed));
    if (liveStatus) {
      liveStatus.classList.remove("offline");
      liveStatus.querySelector("span").textContent = "Live";
    }
  } catch {
    if (liveStatus) {
      liveStatus.classList.add("offline");
      liveStatus.querySelector("span").textContent = "Reconnecting";
    }
  }
}

if (feeds.length) {
  window.setInterval(refreshMessages, 2500);
}
