const boardElement = document.querySelector("#board");
const statusElement = document.querySelector("#status");
const turnLabel = document.querySelector("#turn-label");
const turnIndicator = document.querySelector("#turn-indicator");
const promotionDialog = document.querySelector("#promotion");
const files = "abcdefgh";
const pieceSymbols = {
  white: { queen: "♕", rook: "♖", bishop: "♗", knight: "♘" },
  black: { queen: "♛", rook: "♜", bishop: "♝", knight: "♞" },
};

let gameState = null;
let activeMode = null;
let activeRoomCode = null;
let playerToken = null;
let selectedSquare = null;
let legalMoves = new Set();
let pendingPromotionMove = null;
let previewedRoom = null;
let pollingTimer = null;
let pollInFlight = false;
let moveInFlight = false;
let connectionFailed = false;
let audioContext = null;

const soundPatterns = {
  selection: { notes: [620], spacing: 0, duration: 0.055, volume: 0.025 },
  click: { notes: [480], spacing: 0, duration: 0.045, volume: 0.02 },
  move: { notes: [500, 680], spacing: 0.065, duration: 0.11, volume: 0.032 },
  capture: { notes: [390, 590, 790], spacing: 0.055, duration: 0.14, volume: 0.036 },
  check: { notes: [620, 820, 1040], spacing: 0.075, duration: 0.17, volume: 0.038 },
  checkmate: { notes: [880, 660, 440, 330], spacing: 0.12, duration: 0.2, volume: 0.04 },
  invalid: { notes: [210, 155], spacing: 0.09, duration: 0.12, volume: 0.03 },
};

function unlockAudio() {
  if (!audioContext) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return null;
    try {
      audioContext = new AudioContextClass();
    } catch {
      return null;
    }
  }
  if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
  return audioContext;
}

function playSound(name) {
  const context = unlockAudio();
  const pattern = soundPatterns[name];
  if (!context || !pattern || context.state === "closed") return;
  const start = context.currentTime;
  pattern.notes.forEach((frequency, index) => {
    const toneStart = start + index * pattern.spacing;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(frequency, toneStart);
    gain.gain.setValueAtTime(0, toneStart);
    gain.gain.linearRampToValueAtTime(pattern.volume, toneStart + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.001, toneStart + pattern.duration);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(toneStart);
    oscillator.stop(toneStart + pattern.duration);
  });
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "The local chess server returned an error.");
  }
  return payload;
}

function jsonOptions(payload) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

function showStatus(message, isError = false) {
  statusElement.textContent = message;
  statusElement.classList.toggle("error", isError);
}

function showLobbyError(message) {
  const element = document.querySelector("#lobby-error");
  element.textContent = message;
  element.classList.remove("hidden");
}

function clearLobbyError() {
  const element = document.querySelector("#lobby-error");
  element.textContent = "";
  element.classList.add("hidden");
}

function capitalize(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function defaultStatus(state) {
  if (state.waiting) return "Waiting for the other player to join.";
  if (state.checkmate) return `Checkmate. ${capitalize(state.winner)} wins.`;
  if (state.stalemate) return "Stalemate. The game is a draw.";
  if (state.timed_out) return `Time is up. ${capitalize(state.winner)} wins.`;
  if (state.check) return `Check! ${capitalize(state.turn)} to move.`;
  return `${capitalize(state.turn)} to move.`;
}

function boardSignature(state) {
  return state.pieces
    .map((piece) => `${piece.square}:${piece.color}:${piece.name}`)
    .sort()
    .join("|");
}

function formatClock(seconds) {
  const wholeSeconds = Math.max(0, Math.ceil(seconds || 0));
  const minutes = Math.floor(wholeSeconds / 60);
  const remainder = String(wholeSeconds % 60).padStart(2, "0");
  return `${minutes}:${remainder}`;
}

function updateClocks(state) {
  const clocks = state.clock?.remaining_seconds || {};
  const white = document.querySelector("#white-clock");
  const black = document.querySelector("#black-clock");
  white.textContent = formatClock(clocks.white);
  black.textContent = formatClock(clocks.black);
  const whiteEntry = white.closest(".clock-entry");
  const blackEntry = black.closest(".clock-entry");
  whiteEntry.classList.toggle("active-clock", state.turn === "white" && !state.game_over);
  blackEntry.classList.toggle("active-clock", state.turn === "black" && !state.game_over);
  whiteEntry.classList.toggle("flagged-clock", state.clock?.flagged_color === "white");
  blackEntry.classList.toggle("flagged-clock", state.clock?.flagged_color === "black");
}

function render() {
  if (!gameState) return;
  const pieceAt = new Map(gameState.pieces.map((piece) => [piece.square, piece]));
  boardElement.replaceChildren();

  for (let rank = 8; rank >= 1; rank -= 1) {
    for (let fileIndex = 0; fileIndex < files.length; fileIndex += 1) {
      const square = `${files[fileIndex]}${rank}`;
      const piece = pieceAt.get(square);
      const button = document.createElement("button");
      button.type = "button";
      button.className = `square ${(fileIndex + rank) % 2 === 0 ? "light" : "dark"}`;
      button.setAttribute("role", "gridcell");
      button.setAttribute(
        "aria-label",
        piece ? `${square}, ${piece.color} ${piece.name}` : `${square}, empty`,
      );
      if (square === selectedSquare) button.classList.add("selected");
      if (legalMoves.has(square)) {
        const marker = document.createElement("span");
        marker.className = piece ? "capture-mark" : "legal-dot";
        marker.setAttribute("aria-hidden", "true");
        button.append(marker);
      }
      if (piece) {
        const symbol = document.createElement("span");
        symbol.className = `piece ${piece.color}`;
        symbol.textContent = piece.symbol;
        symbol.setAttribute("aria-hidden", "true");
        button.append(symbol);
      }
      button.addEventListener("click", () => onSquareClick(square));
      boardElement.append(button);
    }
  }

  turnLabel.textContent = gameState.waiting
    ? "Waiting"
    : gameState.game_over
      ? "Game over"
      : capitalize(gameState.turn);
  turnIndicator.classList.toggle("black", gameState.turn === "black");
  updateClocks(gameState);

  const roomBanner = document.querySelector("#room-banner");
  if (gameState.mode === "online" && gameState.room_code) {
    const address = window.location.origin;
    const role = gameState.player_color
      ? `You are ${capitalize(gameState.player_color)}. `
      : "";
    roomBanner.textContent = gameState.waiting
      ? `Room ${gameState.room_code}. Share ${address} and the room code with the other player.`
      : `${role}Room ${gameState.room_code}.`;
    roomBanner.classList.remove("hidden");
  } else {
    roomBanner.classList.add("hidden");
  }
}

function activeStatePath(selected = null) {
  const params = new URLSearchParams();
  if (activeMode === "online") {
    params.set("room", activeRoomCode);
    params.set("token", playerToken);
  }
  if (selected) params.set("from", selected);
  const query = params.toString();
  return `/api/state${query ? `?${query}` : ""}`;
}

async function installGame(state, mode, roomCode = null, token = null) {
  activeMode = mode;
  activeRoomCode = roomCode;
  playerToken = token;
  gameState = state;
  selectedSquare = null;
  legalMoves.clear();
  pendingPromotionMove = null;
  sessionStorage.setItem("chess-mode", mode);
  if (mode === "online") {
    sessionStorage.setItem("chess-room", roomCode);
    sessionStorage.setItem("chess-token", token);
  } else {
    sessionStorage.removeItem("chess-room");
    sessionStorage.removeItem("chess-token");
  }
  document.querySelector("#lobby").classList.add("hidden");
  document.querySelector("#game-screen").classList.remove("hidden");
  document.querySelector("#new-game").classList.remove("hidden");
  render();
  showStatus(defaultStatus(gameState));
  startPolling();
}

function startPolling() {
  if (pollingTimer) clearInterval(pollingTimer);
  pollingTimer = setInterval(pollGameState, 1000);
}

async function pollGameState() {
  if (!activeMode || pollInFlight || moveInFlight) return;
  pollInFlight = true;
  try {
    const previousSignature = gameState ? boardSignature(gameState) : "";
    const previousTurn = gameState?.turn;
    const previousWaiting = gameState?.waiting;
    const freshState = await requestJson(activeStatePath());
    const restoredConnection = connectionFailed;
    connectionFailed = false;
    gameState = freshState;
    if (previousSignature !== boardSignature(freshState)) {
      selectedSquare = null;
      legalMoves.clear();
      render();
      showStatus(defaultStatus(freshState));
    } else {
      if (previousWaiting !== freshState.waiting) render();
      updateClocks(freshState);
      turnLabel.textContent = freshState.waiting
        ? "Waiting"
        : freshState.game_over
          ? "Game over"
          : capitalize(freshState.turn);
      turnIndicator.classList.toggle("black", freshState.turn === "black");
      if (previousWaiting && !freshState.waiting) showStatus(defaultStatus(freshState));
      if (!freshState.game_over && freshState.turn !== previousTurn) {
        showStatus(defaultStatus(freshState));
      }
      if (freshState.game_over) showStatus(defaultStatus(freshState));
      if (restoredConnection) showStatus(defaultStatus(freshState));
    }
  } catch (error) {
    connectionFailed = true;
    showStatus(`Connection lost: ${error.message}`, true);
  } finally {
    pollInFlight = false;
  }
}

async function startHotseat() {
  unlockAudio();
  clearLobbyError();
  try {
    const settings = {
      initial_seconds: Number(document.querySelector("#local-minutes").value),
      increment_seconds: Number(document.querySelector("#local-increment").value),
    };
    const state = await requestJson("/api/new-game", jsonOptions(settings));
    await installGame(state, "hotseat");
  } catch (error) {
    showLobbyError(error.message);
  }
}

async function createRoom() {
  unlockAudio();
  clearLobbyError();
  try {
    const settings = {
      initial_seconds: Number(document.querySelector("#online-minutes").value),
      increment_seconds: Number(document.querySelector("#online-increment").value),
    };
    const response = await requestJson("/api/rooms/create", jsonOptions(settings));
    await installGame(
      response.state,
      "online",
      response.room_code,
      response.player_token,
    );
    playSound("click");
  } catch (error) {
    showLobbyError(error.message);
  }
}

async function previewRoom() {
  clearLobbyError();
  const code = document.querySelector("#join-code").value.trim().toUpperCase();
  if (!code) {
    showLobbyError("Enter a room code first.");
    return;
  }
  try {
    const preview = await requestJson(
      `/api/rooms/preview?code=${encodeURIComponent(code)}`,
    );
    if (preview.started) {
      throw new Error("That room already has two players.");
    }
    previewedRoom = preview.room_code;
    document.querySelector("#room-preview").textContent =
      `Clock: ${formatTimeControl(preview.clock)}. Join this room?`;
    document.querySelector("#room-preview").classList.remove("hidden");
    document.querySelector("#join-room").classList.remove("hidden");
  } catch (error) {
    previewedRoom = null;
    document.querySelector("#room-preview").classList.add("hidden");
    document.querySelector("#join-room").classList.add("hidden");
    showLobbyError(error.message);
    playSound("invalid");
  }
}

function formatTimeControl(clock) {
  const minutes = Math.floor(clock.initial_seconds / 60);
  const extraSeconds = clock.initial_seconds % 60;
  const base = extraSeconds ? `${minutes}:${String(extraSeconds).padStart(2, "0")}` : `${minutes} min`;
  return `${base} + ${clock.increment_seconds} sec per move`;
}

async function joinRoom() {
  clearLobbyError();
  if (!previewedRoom) {
    showLobbyError("Check a room code before joining.");
    return;
  }
  try {
    const response = await requestJson(
      "/api/rooms/join",
      jsonOptions({ room_code: previewedRoom }),
    );
    await installGame(
      response.state,
      "online",
      response.room_code,
      response.player_token,
    );
    playSound("click");
  } catch (error) {
    showLobbyError(error.message);
    playSound("invalid");
  }
}

async function loadState(selected = null) {
  gameState = await requestJson(activeStatePath(selected));
  selectedSquare = selected;
  legalMoves = new Set(gameState.legal_moves || []);
  render();
}

async function onSquareClick(square) {
  unlockAudio();
  if (!gameState || gameState.waiting || gameState.game_over || moveInFlight) return;
  if (
    activeMode === "online"
    && gameState.player_color !== gameState.turn
  ) {
    showStatus("Wait for your turn.");
    playSound("invalid");
    return;
  }
  const piece = gameState.pieces.find((candidate) => candidate.square === square);

  if (!selectedSquare) {
    if (!piece || piece.color !== gameState.turn) {
      showStatus(`Select a ${capitalize(gameState.turn)} piece.`);
      playSound("invalid");
      return;
    }
    playSound("selection");
    try {
      await loadState(square);
      showStatus(legalMoves.size ? "Tap a highlighted square." : "That piece has no legal moves.");
    } catch (error) {
      showStatus(error.message, true);
    }
    return;
  }

  if (square === selectedSquare) {
    playSound("click");
    selectedSquare = null;
    legalMoves.clear();
    render();
    showStatus(defaultStatus(gameState));
    return;
  }

  if (piece && piece.color === gameState.turn) {
    playSound("selection");
    try {
      await loadState(square);
      showStatus(legalMoves.size ? "Tap a highlighted square." : "That piece has no legal moves.");
    } catch (error) {
      showStatus(error.message, true);
    }
    return;
  }

  if (!legalMoves.has(square)) {
    playSound("invalid");
    showStatus(`That move is not legal: ${selectedSquare} to ${square}.`, true);
    return;
  }

  const movingPiece = gameState.pieces.find((candidate) => candidate.square === selectedSquare);
  if (movingPiece?.name === "pawn" && (square.endsWith("1") || square.endsWith("8"))) {
    playSound("click");
    pendingPromotionMove = { from: selectedSquare, to: square };
    const symbols = pieceSymbols[movingPiece.color];
    for (const [name, symbol] of Object.entries(symbols)) {
      promotionDialog.querySelector(`[data-promotion="${name}"]`).textContent = symbol;
    }
    promotionDialog.classList.remove("hidden");
    return;
  }

  await submitMove(selectedSquare, square);
}

async function submitMove(from, to, promotion = null) {
  moveInFlight = true;
  try {
    const move = { from, to, promotion };
    if (activeMode === "online") {
      move.room_code = activeRoomCode;
      move.player_token = playerToken;
    }
    const payload = await requestJson("/api/move", jsonOptions(move));
    gameState = payload.state;
    selectedSquare = null;
    legalMoves.clear();
    render();
    const result = payload.result;
    if (result.checkmate) {
      playSound("checkmate");
      showStatus(`Checkmate. ${capitalize(result.color)} wins.`);
    } else if (result.stalemate) {
      showStatus("Stalemate. The game is a draw.");
    } else if (result.check) {
      playSound("check");
      showStatus(`Check! ${capitalize(gameState.turn)} to move.`);
    } else {
      playSound(result.captured_piece ? "capture" : "move");
      showStatus(defaultStatus(gameState));
    }
  } catch (error) {
    playSound("invalid");
    showStatus(error.message, true);
  } finally {
    moveInFlight = false;
  }
}

function showLobby() {
  if (pollingTimer) clearInterval(pollingTimer);
  pollingTimer = null;
  selectedSquare = null;
  legalMoves.clear();
  document.querySelector("#game-screen").classList.add("hidden");
  document.querySelector("#lobby").classList.remove("hidden");
  document.querySelector("#new-game").classList.add("hidden");
  document.querySelector("#room-preview").classList.add("hidden");
  document.querySelector("#join-room").classList.add("hidden");
  sessionStorage.removeItem("chess-mode");
  sessionStorage.removeItem("chess-room");
  sessionStorage.removeItem("chess-token");
  clearLobbyError();
}

document.querySelector("#start-hotseat").addEventListener("click", startHotseat);
document.querySelector("#create-room").addEventListener("click", createRoom);
document.querySelector("#preview-room").addEventListener("click", previewRoom);
document.querySelector("#join-room").addEventListener("click", joinRoom);
document.querySelector("#join-code").addEventListener("input", () => {
  previewedRoom = null;
  document.querySelector("#room-preview").classList.add("hidden");
  document.querySelector("#join-room").classList.add("hidden");
});
document.querySelector("#new-game").addEventListener("click", showLobby);

document.querySelectorAll("[data-promotion]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!pendingPromotionMove) return;
    const promotion = button.dataset.promotion;
    const move = pendingPromotionMove;
    pendingPromotionMove = null;
    promotionDialog.classList.add("hidden");
    await submitMove(move.from, move.to, promotion);
  });
});

document.querySelector("#cancel-promotion").addEventListener("click", () => {
  playSound("click");
  pendingPromotionMove = null;
  promotionDialog.classList.add("hidden");
});

async function restoreSession() {
  const mode = sessionStorage.getItem("chess-mode");
  if (mode === "online") {
    const roomCode = sessionStorage.getItem("chess-room");
    const token = sessionStorage.getItem("chess-token");
    if (!roomCode || !token) return;
    activeMode = mode;
    activeRoomCode = roomCode;
    playerToken = token;
    try {
      const state = await requestJson(activeStatePath());
      await installGame(state, mode, roomCode, token);
    } catch (error) {
      sessionStorage.removeItem("chess-mode");
      sessionStorage.removeItem("chess-room");
      sessionStorage.removeItem("chess-token");
      activeMode = null;
      activeRoomCode = null;
      playerToken = null;
      showLobbyError(`Could not restore the game: ${error.message}`);
    }
  } else if (mode === "hotseat") {
    try {
      const state = await requestJson("/api/state");
      await installGame(state, "hotseat");
    } catch (error) {
      sessionStorage.removeItem("chess-mode");
      showLobbyError(`Could not restore the game: ${error.message}`);
    }
  }
}

restoreSession();
