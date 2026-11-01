/**
 * Motor Ultra-Ligero Zero-Dependencies (Node.js Nativo + UI WebView + Telegram Bot Sync)
 * Consumo de RAM: ~22 MB | 0 paquetes npm externos | Compatible 1:1 con el motor Rust (src/main.rs)
 */
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const crypto = require("crypto");

const PORT = 8765;
const BASE_DIR = __dirname;
const UI_FILE = path.join(BASE_DIR, "ui", "index.html");
const WORK_DIR = path.join(BASE_DIR, "workspace");
const DOWNLOADS_DIR = path.join(WORK_DIR, "downloads");
const OUTPUTS_DIR = path.join(WORK_DIR, "outputs");
const ENV_FILE = path.join(BASE_DIR, ".env");

fs.mkdirSync(DOWNLOADS_DIR, { recursive: true });
fs.mkdirSync(OUTPUTS_DIR, { recursive: true });

// Estado compartido entre la UI y Telegram
const state = {
  jobs: new Map(),
  jobOrder: [],
  activeJobByChat: new Map(),
  telegramConnected: false,
  config: {
    telegram_bot_token: "",
    allowed_telegram_user_id: "",
    gemini_api_key: "",
    gemini_analysis_model: "gemini-3.8-flash",
    veo_model: "gemini-omni-1.1-flash",
    tts_provider: "edge",
    edge_tts_voice: "es-MX-DaliaNeural",
    elevenlabs_api_key: "",
    elevenlabs_voice_id: "",
    default_content_style:
      "Video corto dinámico (Reel/Short/TikTok) en español, con un gancho fuerte en los primeros 3 segundos, tono profesional pero cercano, recreado con visuales generados por IA.",
  },
};

function loadEnv() {
  if (!fs.existsSync(ENV_FILE)) return;
  const lines = fs.readFileSync(ENV_FILE, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const idx = trimmed.indexOf("=");
    const k = trimmed.slice(0, idx).trim();
    const v = trimmed.slice(idx + 1).trim();
    if (k === "TELEGRAM_BOT_TOKEN") state.config.telegram_bot_token = v;
    if (k === "ALLOWED_TELEGRAM_USER_ID") state.config.allowed_telegram_user_id = v;
    if (k === "GEMINI_API_KEY") state.config.gemini_api_key = v;
    if (k === "GEMINI_ANALYSIS_MODEL" && v) state.config.gemini_analysis_model = v;
    if (k === "VEO_MODEL" && v) state.config.veo_model = v;
    if (k === "TTS_PROVIDER" && v) state.config.tts_provider = v;
    if (k === "EDGE_TTS_VOICE" && v) state.config.edge_tts_voice = v;
    if (k === "ELEVENLABS_API_KEY") state.config.elevenlabs_api_key = v;
    if (k === "ELEVENLABS_VOICE_ID") state.config.elevenlabs_voice_id = v;
    if (k === "DEFAULT_CONTENT_STYLE" && v) state.config.default_content_style = v;
  }
}

function saveEnv() {
  const c = state.config;
  const content = [
    `TELEGRAM_BOT_TOKEN=${c.telegram_bot_token}`,
    `ALLOWED_TELEGRAM_USER_ID=${c.allowed_telegram_user_id}`,
    `GEMINI_API_KEY=${c.gemini_api_key}`,
    `GEMINI_ANALYSIS_MODEL=${c.gemini_analysis_model}`,
    `VEO_MODEL=${c.veo_model}`,
    `TTS_PROVIDER=${c.tts_provider}`,
    `EDGE_TTS_VOICE=${c.edge_tts_voice}`,
    `ELEVENLABS_API_KEY=${c.elevenlabs_api_key}`,
    `ELEVENLABS_VOICE_ID=${c.elevenlabs_voice_id}`,
    `DEFAULT_CONTENT_STYLE=${(c.default_content_style || "").replace(/\r?\n/g, " ")}`,
  ].join("\n");
  fs.writeFileSync(ENV_FILE, content, "utf8");
}

function loadExistingProposals() {
  const proposalsDir = path.join(WORK_DIR, "proposals");
  if (!fs.existsSync(proposalsDir)) return;
  const files = fs.readdirSync(proposalsDir).filter((f) => f.endsWith(".json"));
  for (const file of files) {
    try {
      const raw = JSON.parse(fs.readFileSync(path.join(proposalsDir, file), "utf8"));
      const p = raw.proposal || {};
      const jobId = p.job_id || path.basename(file, ".json");
      const finalFile = path.join(OUTPUTS_DIR, `${jobId}_final.mp4`);
      const hasFinal = fs.existsSync(finalFile);
      const job = {
        job_id: jobId,
        url: p.source_url || "",
        platform: p.platform || detectPlatform(p.source_url || ""),
        title: p.new_title || "Video Analizado",
        status: hasFinal ? "completed" : "awaiting_approval",
        error: null,
        source_video_url: `/media/downloads/${jobId}.mp4`,
        final_video_url: hasFinal ? `/media/outputs/${jobId}_final.mp4` : null,
        engine_used: hasFinal ? "IA Video Generator" : null,
        original_analysis: p.original_analysis || "",
        new_title: p.new_title || "",
        voiceover_script: p.voiceover_script || "",
        visual_prompt: p.visual_prompt || "",
        scenes: p.scenes || [],
        social_caption: p.social_caption || "",
        chat_id: null,
      };
      state.jobs.set(jobId, job);
      if (!state.jobOrder.includes(jobId)) state.jobOrder.push(jobId);
    } catch {}
  }
}

loadEnv();
loadExistingProposals();

function detectPlatform(url) {
  const u = (url || "").toLowerCase();
  if (u.includes("youtube.com") || u.includes("youtu.be")) return "YouTube";
  if (u.includes("instagram.com")) return "Instagram";
  if (u.includes("facebook.com") || u.includes("fb.watch")) return "Facebook";
  if (u.includes("tiktok.com")) return "TikTok";
  return "Web";
}

function runPythonCli(args) {
  return new Promise((resolve, reject) => {
    const env = {
      ...process.env,
      TELEGRAM_BOT_TOKEN: state.config.telegram_bot_token,
      GEMINI_API_KEY: state.config.gemini_api_key,
      GEMINI_ANALYSIS_MODEL: state.config.gemini_analysis_model,
      VEO_MODEL: state.config.veo_model,
      TTS_PROVIDER: state.config.tts_provider,
      EDGE_TTS_VOICE: state.config.edge_tts_voice,
      ELEVENLABS_API_KEY: state.config.elevenlabs_api_key,
      ELEVENLABS_VOICE_ID: state.config.elevenlabs_voice_id,
      DEFAULT_CONTENT_STYLE: state.config.default_content_style,
      PYTHONIOENCODING: "utf-8",
    };
    const proc = spawn("python", ["cli.py", ...args], { cwd: BASE_DIR, env });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d.toString()));
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("close", (code) => {
      if (code === 0) {
        try {
          const startIdx = stdout.indexOf("{");
          const endIdx = stdout.lastIndexOf("}");
          const cleanJson =
            startIdx !== -1 && endIdx !== -1
              ? stdout.slice(startIdx, endIdx + 1)
              : stdout.trim();
          resolve(JSON.parse(cleanJson));
        } catch (err) {
          reject(new Error(`Salida inválida de Python: ${stdout}`));
        }
      } else {
        reject(new Error(stderr.trim() || `Código de salida ${code}`));
      }
    });
  });
}

// --- Funciones de Telegram API nativa (HTTPS sin librerías pesadas) ---
function tgApiRequest(method, payload) {
  return new Promise((resolve) => {
    const token = state.config.telegram_bot_token;
    if (!token || token === "tu_token_de_telegram_aqui") return resolve(null);
    const body = JSON.stringify(payload || {});
    const req = https.request(
      {
        hostname: "api.telegram.org",
        path: `/bot${token}/${method}`,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
        },
        timeout: 25000,
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          try {
            resolve(JSON.parse(data));
          } catch {
            resolve(null);
          }
        });
      }
    );
    req.on("error", () => resolve(null));
    req.write(body);
    req.end();
  });
}

function formatTelegramProposal(job) {
  return (
    `🎬 <b>Propuesta Lista (${job.platform})</b> [#${job.job_id}]\n\n` +
    `🔍 <b>Análisis Original:</b>\n${job.original_analysis}\n\n` +
    `📌 <b>Nuevo Título:</b> ${job.new_title}\n\n` +
    `🎙️ <b>Guion (Voz en Off):</b>\n<i>${job.voiceover_script}</i>\n\n` +
    `🎨 <b>Prompt de Video IA:</b>\n<code>${job.visual_prompt}</code>\n\n` +
    `📲 <b>Copy Sugerido:</b>\n${job.social_caption}`
  );
}

function approvalKeyboard(jobId) {
  return {
    inline_keyboard: [
      [{ text: "✅ Aprobar y Generar Video IA", callback_data: `approve:${jobId}` }],
      [
        { text: "✏️ Editar con IA", callback_data: `edit:${jobId}` },
        { text: "❌ Cancelar", callback_data: `cancel:${jobId}` },
      ],
    ],
  };
}

// --- Orquestación del Flujo ---
async function startJobPipeline(url, style, chatId = null) {
  const jobId = crypto.randomUUID().slice(0, 8);
  const effectiveStyle = (style || "").trim() || state.config.default_content_style;

  const job = {
    job_id: jobId,
    url,
    platform: detectPlatform(url),
    title: "Descargando video...",
    status: "downloading",
    error: null,
    source_video_url: null,
    final_video_url: null,
    engine_used: null,
    original_analysis: "",
    new_title: "",
    voiceover_script: "",
    visual_prompt: "",
    scenes: [],
    social_caption: "",
    chat_id: chatId,
  };

  state.jobs.set(jobId, job);
  state.jobOrder.push(jobId);

  (async () => {
    try {
      if (chatId) {
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: `⬇️ Descargando y analizando video de <b>${job.platform}</b> (Job <code>#${jobId}</code>)...`,
          parse_mode: "HTML",
        });
      }

      job.status = "analyzing";
      const result = await runPythonCli([
        "analyze",
        "--url",
        url,
        "--style",
        effectiveStyle,
        "--job-id",
        jobId,
      ]);

      const p = result.proposal || {};
      job.status = "awaiting_approval";
      job.original_analysis = p.original_analysis || "";
      job.new_title = p.new_title || "Nuevo Video IA";
      job.title = job.new_title;
      job.voiceover_script = p.voiceover_script || "";
      job.visual_prompt = p.visual_prompt || "";
      job.scenes = p.scenes || [];
      job.social_caption = p.social_caption || "";
      job.source_video_url = `/media/downloads/${jobId}.mp4`;

      if (chatId) {
        state.activeJobByChat.set(chatId, jobId);
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: formatTelegramProposal(job),
          parse_mode: "HTML",
          reply_markup: approvalKeyboard(jobId),
        });
      }
    } catch (err) {
      job.status = "error";
      job.error = err.message;
      if (chatId) {
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: `❌ Error en Job #${jobId}: ${err.message}`,
        });
      }
    }
  })();

  return jobId;
}

async function triggerGeneration(jobId) {
  const job = state.jobs.get(jobId);
  if (!job) return;
  job.status = "generating";
  job.error = null;

  (async () => {
    try {
      if (job.chat_id) {
        await tgApiRequest("sendMessage", {
          chat_id: job.chat_id,
          text: `🎬 <b>Generando video IA para #${jobId}...</b>\nTe avisaré en cuanto esté listo.`,
          parse_mode: "HTML",
        });
      }

      const res = await runPythonCli([
        "generate",
        "--job-id",
        jobId,
        "--script",
        job.voiceover_script,
        "--prompt",
        job.visual_prompt,
      ]);

      job.status = "completed";
      job.engine_used = res.engine_used || "IA Video Generator";
      job.final_video_url = `/media/outputs/${jobId}_final.mp4`;

      if (job.chat_id) {
        await tgApiRequest("sendMessage", {
          chat_id: job.chat_id,
          text:
            `✅ <b>¡Video IA Terminado! (#${jobId})</b>\n` +
            `🛠 Motor: <i>${job.engine_used}</i>\n` +
            `📂 Archivo guardado en: <code>workspace/outputs/${jobId}_final.mp4</code>\n\n` +
            `📲 <b>Copy para publicar:</b>\n${job.social_caption}`,
          parse_mode: "HTML",
        });
      }
    } catch (err) {
      job.status = "error";
      job.error = err.message;
      if (job.chat_id) {
        await tgApiRequest("sendMessage", {
          chat_id: job.chat_id,
          text: `❌ Error generando video #${jobId}: ${err.message}`,
        });
      }
    }
  })();
}

// --- Loop Ligero de Telegram ---
async function pollTelegram() {
  let offset = 0;
  while (true) {
    const token = state.config.telegram_bot_token;
    if (!token || token === "tu_token_de_telegram_aqui") {
      state.telegramConnected = false;
      await new Promise((r) => setTimeout(r, 3500));
      continue;
    }

    const resp = await tgApiRequest("getUpdates", { timeout: 15, offset });
    if (resp && resp.ok) {
      state.telegramConnected = true;
      for (const upd of resp.result || []) {
        offset = upd.update_id + 1;
        await handleTelegramUpdate(upd);
      }
    } else {
      state.telegramConnected = false;
      await new Promise((r) => setTimeout(r, 3500));
    }
  }
}

async function handleTelegramUpdate(upd) {
  const allowedId = (state.config.allowed_telegram_user_id || "").trim();

  if (upd.message && upd.message.text) {
    const msg = upd.message;
    const chatId = msg.chat.id;
    const userId = String(msg.from?.id || "");
    if (allowedId && userId !== allowedId) return;

    const text = msg.text.trim();
    const urlMatch = text.match(/https?:\/\/[^\s]+/);

    if (text.startsWith("/start")) {
      await tgApiRequest("sendMessage", {
        chat_id: chatId,
        text:
          "⚡ <b>AI Video Recreator Studio Conectado</b>\n\n" +
          "Envíame un link de <b>Facebook, Instagram, YouTube o TikTok</b>.\n" +
          "Verás el progreso tanto aquí en Telegram como en tu UI de escritorio.",
        parse_mode: "HTML",
      });
      return;
    }

    if (urlMatch) {
      const url = urlMatch[0];
      const extraStyle = text.replace(url, "").trim();
      await startJobPipeline(url, extraStyle, chatId);
      return;
    }

    const activeJobId = state.activeJobByChat.get(chatId);
    if (activeJobId && state.jobs.has(activeJobId)) {
      const job = state.jobs.get(activeJobId);
      await tgApiRequest("sendMessage", {
        chat_id: chatId,
        text: `✨ Refinando guion y prompt del Job #${activeJobId}...`,
      });
      try {
        const res = await runPythonCli([
          "refine",
          "--job-id",
          activeJobId,
          "--feedback",
          text,
        ]);
        const p = res.proposal || {};
        job.new_title = p.new_title || job.new_title;
        job.voiceover_script = p.voiceover_script || job.voiceover_script;
        job.visual_prompt = p.visual_prompt || job.visual_prompt;
        job.social_caption = p.social_caption || job.social_caption;
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: formatTelegramProposal(job),
          parse_mode: "HTML",
          reply_markup: approvalKeyboard(activeJobId),
        });
      } catch (e) {
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: `❌ Error refinando: ${e.message}`,
        });
      }
    }
  }

  if (upd.callback_query) {
    const cb = upd.callback_query;
    const chatId = cb.message?.chat?.id;
    const [action, jobId] = (cb.data || "").split(":");
    await tgApiRequest("answerCallbackQuery", { callback_query_id: cb.id });
    if (!state.jobs.has(jobId)) return;

    if (action === "approve") {
      await triggerGeneration(jobId);
    } else if (action === "cancel") {
      state.jobs.get(jobId).status = "cancelled";
      await tgApiRequest("sendMessage", {
        chat_id: chatId,
        text: `🚫 Job #${jobId} cancelado.`,
      });
    } else if (action === "edit") {
      state.activeJobByChat.set(chatId, jobId);
      await tgApiRequest("sendMessage", {
        chat_id: chatId,
        text: `✏️ Escribe los cambios que quieres hacerle al guion o al prompt del Job #${jobId}:`,
      });
    }
  }
}

// --- Servidor HTTP Ligero con soporte de Streaming MP4 ---
function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => {
      try {
        resolve(JSON.parse(data || "{}"));
      } catch {
        resolve({});
      }
    });
  });
}

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  const urlObj = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const pathname = urlObj.pathname;

  if (req.method === "GET" && pathname === "/") {
    const html = fs.readFileSync(UI_FILE, "utf8");
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(html);
  }

  if (req.method === "GET" && pathname === "/api/state") {
    const jobsList = [...state.jobOrder]
      .reverse()
      .map((id) => state.jobs.get(id))
      .filter(Boolean);
    return sendJson(res, 200, {
      jobs: jobsList,
      telegram_connected: state.telegramConnected,
      config: {
        has_telegram_token:
          Boolean(state.config.telegram_bot_token) &&
          state.config.telegram_bot_token !== "tu_token_de_telegram_aqui",
        has_gemini_key:
          Boolean(state.config.gemini_api_key) &&
          state.config.gemini_api_key !== "tu_gemini_api_key_aqui",
        allowed_telegram_user_id: state.config.allowed_telegram_user_id,
        gemini_analysis_model: state.config.gemini_analysis_model,
        veo_model: state.config.veo_model,
        tts_provider: state.config.tts_provider,
        edge_tts_voice: state.config.edge_tts_voice,
        default_content_style: state.config.default_content_style,
      },
    });
  }

  if (req.method === "POST" && pathname === "/api/config") {
    const body = await readBody(req);
    if (body.telegram_bot_token && body.telegram_bot_token.trim()) {
      state.config.telegram_bot_token = body.telegram_bot_token.trim();
    }
    if (typeof body.allowed_telegram_user_id === "string") {
      state.config.allowed_telegram_user_id = body.allowed_telegram_user_id.trim();
    }
    if (body.gemini_api_key && body.gemini_api_key.trim()) {
      state.config.gemini_api_key = body.gemini_api_key.trim();
    }
    if (body.tts_provider) state.config.tts_provider = body.tts_provider.trim();
    if (body.edge_tts_voice) state.config.edge_tts_voice = body.edge_tts_voice.trim();
    if (typeof body.default_content_style === "string" && body.default_content_style.trim()) {
      state.config.default_content_style = body.default_content_style.trim();
    }
    saveEnv();
    return sendJson(res, 200, { ok: true });
  }

  if (req.method === "POST" && pathname === "/api/analyze") {
    const body = await readBody(req);
    if (!body.url) return sendJson(res, 400, { error: "Falta URL" });
    const jobId = await startJobPipeline(body.url, body.style || "");
    return sendJson(res, 202, { job_id: jobId });
  }

  const jobMatch = pathname.match(/^\/api\/jobs\/([^/]+)\/(edit|refine|approve|cancel)$/);
  if (req.method === "POST" && jobMatch) {
    const [, jobId, action] = jobMatch;
    const job = state.jobs.get(jobId);
    if (!job) return sendJson(res, 404, { error: "Job no encontrado" });

    if (action === "edit") {
      const b = await readBody(req);
      if (typeof b.new_title === "string") job.new_title = b.new_title;
      if (typeof b.voiceover_script === "string") job.voiceover_script = b.voiceover_script;
      if (typeof b.visual_prompt === "string") job.visual_prompt = b.visual_prompt;
      if (typeof b.social_caption === "string") job.social_caption = b.social_caption;
      return sendJson(res, 200, { ok: true });
    }

    if (action === "refine") {
      const b = await readBody(req);
      try {
        const out = await runPythonCli([
          "refine",
          "--job-id",
          jobId,
          "--feedback",
          b.feedback || "",
        ]);
        const p = out.proposal || {};
        job.new_title = p.new_title || job.new_title;
        job.voiceover_script = p.voiceover_script || job.voiceover_script;
        job.visual_prompt = p.visual_prompt || job.visual_prompt;
        job.social_caption = p.social_caption || job.social_caption;
        return sendJson(res, 200, { ok: true });
      } catch (e) {
        return sendJson(res, 500, { error: e.message });
      }
    }

    if (action === "approve") {
      await triggerGeneration(jobId);
      return sendJson(res, 200, { ok: true });
    }

    if (action === "cancel") {
      job.status = "cancelled";
      return sendJson(res, 200, { ok: true });
    }
  }

  if (req.method === "GET" && pathname.startsWith("/media/")) {
    const rel = pathname.replace("/media/", "");
    const safePath = path.normalize(path.join(WORK_DIR, rel));
    if (!safePath.startsWith(WORK_DIR) || !fs.existsSync(safePath)) {
      res.writeHead(404);
      return res.end("Not found");
    }
    const stat = fs.statSync(safePath);
    const range = req.headers.range;
    if (range) {
      const parts = range.replace(/bytes=/, "").split("-");
      const start = parseInt(parts[0], 10);
      const end = parts[1] ? parseInt(parts[1], 10) : stat.size - 1;
      res.writeHead(206, {
        "Content-Range": `bytes ${start}-${end}/${stat.size}`,
        "Accept-Ranges": "bytes",
        "Content-Length": end - start + 1,
        "Content-Type": "video/mp4",
      });
      return fs.createReadStream(safePath, { start, end }).pipe(res);
    }
    res.writeHead(200, {
      "Content-Length": stat.size,
      "Content-Type": "video/mp4",
    });
    return fs.createReadStream(safePath).pipe(res);
  }

  res.writeHead(404);
  res.end("Not found");
});

server.listen(PORT, "127.0.0.1", () => {
  const url = `http://127.0.0.1:${PORT}`;
  console.log(`⚡ AI Video Recreator Studio corriendo en ${url}`);
  if (!process.argv.includes("--no-open")) {
    spawn("cmd", ["/c", "start", "msedge", `--app=${url}`], {
      detached: true,
      stdio: "ignore",
    }).unref();
  }
  pollTelegram();
});
