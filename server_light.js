/**
 * Motor Ultra-Ligero Zero-Dependencies (Node.js Nativo + UI WebView + Telegram Bot Sync)
 * Pipeline Aura / Manga Impact Edit (6 Pasos: Ingesta -> Análisis Multimodal -> Arte Manga/Baki -> Storyboard Telegram/UI -> Ensamblado FFmpeg Zoom/Shake + Phonk/SFX -> Entrega MP4)
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
const FRAMES_DIR = path.join(WORK_DIR, "frames");
const STORYBOARDS_DIR = path.join(WORK_DIR, "storyboards");
const OUTPUTS_DIR = path.join(WORK_DIR, "outputs");
const ENV_FILE = path.join(BASE_DIR, ".env");

for (const d of [DOWNLOADS_DIR, FRAMES_DIR, STORYBOARDS_DIR, OUTPUTS_DIR]) {
  fs.mkdirSync(d, { recursive: true });
}

const state = {
  jobs: new Map(),
  jobOrder: [],
  activeJobByChat: new Map(),
  telegramConnected: false,
  config: {
    telegram_bot_token: "",
    allowed_telegram_user_id: "",
    gemini_api_key: "",
    gemini_analysis_model: "gemini-2.5-flash",
    art_provider: "auto",
    sd_webui_url: "http://127.0.0.1:7860",
    comfyui_url: "http://127.0.0.1:8188",
    manga_lora_tag: "<lora:baki_manga_style:0.85>",
    img2img_denoising: "0.62",
    impact_hold_seconds: "1.15",
    default_content_style:
      "Edición de impacto estilo Manga Seinen / Baki Hanma / Berserk: trazos de tinta negra agresivos, sombreado dramático cross-hatching, líneas cinéticas de velocidad (speedlines), aura de combate intensa, blanco y negro de ultra alto contraste.",
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
    if (k === "ART_PROVIDER" && v) state.config.art_provider = v;
    if (k === "SD_WEBUI_URL" && v) state.config.sd_webui_url = v;
    if (k === "COMFYUI_URL" && v) state.config.comfyui_url = v;
    if (k === "MANGA_LORA_TAG" && v) state.config.manga_lora_tag = v;
    if (k === "IMG2IMG_DENOISING" && v) state.config.img2img_denoising = v;
    if (k === "IMPACT_HOLD_SECONDS" && v) state.config.impact_hold_seconds = v;
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
    `ART_PROVIDER=${c.art_provider}`,
    `SD_WEBUI_URL=${c.sd_webui_url}`,
    `COMFYUI_URL=${c.comfyui_url}`,
    `MANGA_LORA_TAG=${c.manga_lora_tag}`,
    `IMG2IMG_DENOISING=${c.img2img_denoising}`,
    `IMPACT_HOLD_SECONDS=${c.impact_hold_seconds}`,
    `DEFAULT_CONTENT_STYLE=${(c.default_content_style || "").replace(/\r?\n/g, " ")}`,
  ].join("\n");
  fs.writeFileSync(ENV_FILE, content, "utf8");
}

function detectPlatform(url) {
  const u = (url || "").toLowerCase();
  if (u.includes("youtube.com") || u.includes("youtu.be")) return "YouTube";
  if (u.includes("instagram.com")) return "Instagram";
  if (u.includes("facebook.com") || u.includes("fb.watch")) return "Facebook";
  if (u.includes("tiktok.com")) return "TikTok";
  return "Web";
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
        title: p.new_title || "Manga Aura Edit",
        status: hasFinal ? "completed" : "awaiting_approval",
        error: null,
        source_video_url: `/media/downloads/${jobId}.mp4`,
        final_video_url: hasFinal ? `/media/outputs/${jobId}_final.mp4` : null,
        engine_used: hasFinal ? "Manga Impact + Phonk/SFX (9:16)" : null,
        original_analysis: p.original_analysis || "",
        subjects_and_roles: p.subjects_and_roles || "",
        new_title: p.new_title || "",
        voiceover_script: p.voiceover_script || "",
        visual_prompt: p.visual_prompt || "",
        scenes: p.scenes || [],
        impact_moments: p.impact_moments || [],
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

function runPythonCli(args) {
  return new Promise((resolve, reject) => {
    const env = {
      ...process.env,
      TELEGRAM_BOT_TOKEN: state.config.telegram_bot_token,
      GEMINI_API_KEY: state.config.gemini_api_key,
      GEMINI_ANALYSIS_MODEL: state.config.gemini_analysis_model,
      ART_PROVIDER: state.config.art_provider,
      SD_WEBUI_URL: state.config.sd_webui_url,
      COMFYUI_URL: state.config.comfyui_url,
      MANGA_LORA_TAG: state.config.manga_lora_tag,
      IMG2IMG_DENOISING: state.config.img2img_denoising,
      IMPACT_HOLD_SECONDS: state.config.impact_hold_seconds,
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
        } catch {
          reject(new Error(`Salida inválida de Python: ${stdout}`));
        }
      } else {
        reject(new Error(stderr.trim() || `Código de salida ${code}`));
      }
    });
  });
}

// --- Funciones de Telegram API nativa (JSON + Multipart Upload para fotos y videos) ---
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

function tgUploadFile(method, fieldName, filePath, fields = {}) {
  return new Promise((resolve) => {
    const token = state.config.telegram_bot_token;
    if (!token || token === "tu_token_de_telegram_aqui" || !fs.existsSync(filePath)) {
      return resolve(null);
    }
    const boundary = "----AuraBoundary" + crypto.randomUUID().replace(/-/g, "");
    const parts = [];
    for (const [k, v] of Object.entries(fields)) {
      parts.push(
        Buffer.from(
          `--${boundary}\r\nContent-Disposition: form-data; name="${k}"\r\n\r\n${v}\r\n`,
          "utf8"
        )
      );
    }
    const filename = path.basename(filePath);
    const mime = filename.endsWith(".mp4") ? "video/mp4" : "image/jpeg";
    parts.push(
      Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="${fieldName}"; filename="${filename}"\r\nContent-Type: ${mime}\r\n\r\n`,
        "utf8"
      )
    );
    parts.push(fs.readFileSync(filePath));
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`, "utf8"));
    const body = Buffer.concat(parts);

    const req = https.request(
      {
        hostname: "api.telegram.org",
        path: `/bot${token}/${method}`,
        method: "POST",
        headers: {
          "Content-Type": `multipart/form-data; boundary=${boundary}`,
          "Content-Length": body.length,
        },
        timeout: 120000,
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
  const momentsText = (job.impact_moments || [])
    .map(
      (m) =>
        `  ⚡ <b>Panel #${m.index} [${m.timestamp_label}] — ${m.moment_title}</b>\n` +
        `      <i>${m.subjects_action}</i>\n` +
        `      🎨 Motor: <code>${m.art_engine || "Manga Engine"}</code>`
    )
    .join("\n\n");

  return (
    `🔥 <b>STORYBOARD MANGA / AURA EDIT (${job.platform})</b> [#${job.job_id}]\n\n` +
    `📌 <b>Título:</b> ${job.new_title}\n\n` +
    `🥊 <b>Sujetos y Roles:</b>\n${job.subjects_and_roles || "Enfrentamiento principal"}\n\n` +
    `🔍 <b>Análisis de Tensión:</b>\n${job.original_analysis}\n\n` +
    `⏱️ <b>Timestamps de Clímax:</b>\n${momentsText}\n\n` +
    `📲 <b>Copy:</b>\n${job.social_caption}`
  );
}

function approvalKeyboard(jobId, numPanels = 3) {
  const regenRow = [];
  for (let i = 1; i <= Math.min(numPanels || 3, 4); i++) {
    regenRow.push({
      text: `🔄 Regen #${i}`,
      callback_data: `regen:${jobId}:${i}`,
    });
  }
  return {
    inline_keyboard: [
      [{ text: "✅ Aprobar y Ensamblar Video (9:16)", callback_data: `approve:${jobId}` }],
      regenRow,
      [
        { text: "🔄 Regenerar Todas", callback_data: `regen:${jobId}:0` },
        { text: "✏️ Editar Prompts/Tiempos", callback_data: `edit:${jobId}` },
      ],
      [{ text: "❌ Cancelar", callback_data: `cancel:${jobId}` }],
    ],
  };
}

async function sendStoryboardsToTelegram(chatId, job) {
  if (!chatId) return;
  for (const m of job.impact_moments || []) {
    if (m.storyboard_image_path && fs.existsSync(m.storyboard_image_path)) {
      await tgUploadFile("sendPhoto", "photo", m.storyboard_image_path, {
        chat_id: String(chatId),
        caption: `⚡ Panel #${m.index} [${m.timestamp_label}] — ${m.moment_title}\n${m.subjects_action}`,
      });
    }
  }
}

function applyProposalToJob(job, p) {
  job.original_analysis = p.original_analysis || job.original_analysis;
  job.subjects_and_roles = p.subjects_and_roles || job.subjects_and_roles;
  job.new_title = p.new_title || job.new_title || "Manga Aura Edit";
  job.title = job.new_title;
  job.voiceover_script = p.voiceover_script || job.voiceover_script;
  job.visual_prompt = p.visual_prompt || job.visual_prompt;
  job.scenes = p.scenes || job.scenes;
  job.impact_moments = p.impact_moments || job.impact_moments || [];
  job.social_caption = p.social_caption || job.social_caption;
}

// --- Orquestación del Pipeline (Pasos 1 a 6) ---
async function startJobPipeline(url, style, chatId = null) {
  const jobId = crypto.randomUUID().slice(0, 8);
  const effectiveStyle = (style || "").trim() || state.config.default_content_style;

  const job = {
    job_id: jobId,
    url,
    platform: detectPlatform(url),
    title: "Paso 1: Descargando video base...",
    status: "downloading",
    error: null,
    source_video_url: null,
    final_video_url: null,
    engine_used: null,
    original_analysis: "",
    subjects_and_roles: "",
    new_title: "",
    voiceover_script: "",
    visual_prompt: "",
    scenes: [],
    impact_moments: [],
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
          text: `⬇️ <b>Paso 1-3:</b> Descargando video de <b>${job.platform}</b>, extrayendo timestamps de clímax y generando paneles Manga/Baki (Job <code>#${jobId}</code>)...`,
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
      applyProposalToJob(job, p);
      job.source_video_url = `/media/downloads/${jobId}.mp4`;

      if (chatId) {
        state.activeJobByChat.set(chatId, jobId);
        await sendStoryboardsToTelegram(chatId, job);
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: formatTelegramProposal(job),
          parse_mode: "HTML",
          reply_markup: approvalKeyboard(jobId, (job.impact_moments || []).length),
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
          text: `🎬 <b>Paso 5/6 (#${jobId}):</b> Cortando en timestamps clave, insertando paneles Manga con Zoom/Shake y montando audio Phonk + SFX de golpe...`,
          parse_mode: "HTML",
        });
      }

      const res = await runPythonCli([
        "generate",
        "--job-id",
        jobId,
        "--script",
        job.voiceover_script || "",
        "--prompt",
        job.visual_prompt || "",
      ]);

      job.status = "completed";
      job.engine_used = res.engine_used || "Manga Impact + Phonk/SFX (9:16)";
      job.final_video_url = `/media/outputs/${jobId}_final.mp4?t=${Date.now()}`;

      const finalFilePath = path.join(OUTPUTS_DIR, `${jobId}_final.mp4`);
      if (job.chat_id) {
        if (fs.existsSync(finalFilePath)) {
          await tgUploadFile("sendVideo", "video", finalFilePath, {
            chat_id: String(job.chat_id),
            caption:
              `🔥 ${job.new_title}\n` +
              `🥊 Sujetos: ${job.subjects_and_roles}\n` +
              `🛠 ${job.engine_used}\n\n` +
              `${job.social_caption}`.slice(0, 950),
          });
        } else {
          await tgApiRequest("sendMessage", {
            chat_id: job.chat_id,
            text:
              `✅ <b>¡Aura Edit Terminado! (#${jobId})</b>\n` +
              `🛠 Motor: <i>${job.engine_used}</i>\n` +
              `📂 Archivo: <code>workspace/outputs/${jobId}_final.mp4</code>`,
            parse_mode: "HTML",
          });
        }
      }
    } catch (err) {
      job.status = "error";
      job.error = err.message;
      if (job.chat_id) {
        await tgApiRequest("sendMessage", {
          chat_id: job.chat_id,
          text: `❌ Error ensamblando video #${jobId}: ${err.message}`,
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
          "🔥 <b>AI Manga / Aura Impact Studio Conectado</b>\n\n" +
          "Envíame un link de <b>Facebook, Instagram, YouTube o TikTok</b>.\n" +
          "Extraeré los timestamps de clímax, identificaré los sujetos y te enviaré los storyboards estilo Manga/Baki para tu aprobación.",
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
        text: `🎨 Ajustando prompts/timestamps y regenerando storyboards del Job #${activeJobId}...`,
      });
      try {
        const res = await runPythonCli([
          "refine",
          "--job-id",
          activeJobId,
          "--feedback",
          text,
        ]);
        applyProposalToJob(job, res.proposal || {});
        await sendStoryboardsToTelegram(chatId, job);
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: formatTelegramProposal(job),
          parse_mode: "HTML",
          reply_markup: approvalKeyboard(activeJobId, (job.impact_moments || []).length),
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
    const parts = (cb.data || "").split(":");
    const action = parts[0];
    const jobId = parts[1];
    await tgApiRequest("answerCallbackQuery", { callback_query_id: cb.id });
    if (!state.jobs.has(jobId)) return;
    const job = state.jobs.get(jobId);

    if (action === "approve") {
      await triggerGeneration(jobId);
    } else if (action === "cancel") {
      job.status = "cancelled";
      await tgApiRequest("sendMessage", {
        chat_id: chatId,
        text: `🚫 Job #${jobId} cancelado.`,
      });
    } else if (action === "edit") {
      state.activeJobByChat.set(chatId, jobId);
      await tgApiRequest("sendMessage", {
        chat_id: chatId,
        text: `✏️ Escribe los cambios de prompt, estilo o segundo de corte para el Job #${jobId}:`,
      });
    } else if (action === "regen") {
      const panelNum = parseInt(parts[2] || "0", 10);
      await tgApiRequest("sendMessage", {
        chat_id: chatId,
        text: `🔄 Regenerando ${panelNum > 0 ? "Panel #" + panelNum : "todos los paneles"} con nueva semilla...`,
      });
      try {
        const out = await runPythonCli([
          "regenerate",
          "--job-id",
          jobId,
          "--panel",
          String(panelNum),
        ]);
        applyProposalToJob(job, out.proposal || {});
        await sendStoryboardsToTelegram(chatId, job);
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: formatTelegramProposal(job),
          parse_mode: "HTML",
          reply_markup: approvalKeyboard(jobId, (job.impact_moments || []).length),
        });
      } catch (e) {
        await tgApiRequest("sendMessage", {
          chat_id: chatId,
          text: `❌ Error regenerando imagen: ${e.message}`,
        });
      }
    }
  }
}

// --- Servidor HTTP Ligero con soporte de Streaming MP4 e Imágenes Storyboard ---
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
        art_provider: state.config.art_provider,
        sd_webui_url: state.config.sd_webui_url,
        comfyui_url: state.config.comfyui_url,
        manga_lora_tag: state.config.manga_lora_tag,
        img2img_denoising: state.config.img2img_denoising,
        impact_hold_seconds: state.config.impact_hold_seconds,
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
    if (body.art_provider) state.config.art_provider = body.art_provider.trim();
    if (body.sd_webui_url) state.config.sd_webui_url = body.sd_webui_url.trim();
    if (body.comfyui_url) state.config.comfyui_url = body.comfyui_url.trim();
    if (typeof body.manga_lora_tag === "string") {
      state.config.manga_lora_tag = body.manga_lora_tag.trim();
    }
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

  const jobMatch = pathname.match(
    /^\/api\/jobs\/([^/]+)\/(edit|refine|regenerate|approve|cancel)$/
  );
  if (req.method === "POST" && jobMatch) {
    const [, jobId, action] = jobMatch;
    const job = state.jobs.get(jobId);
    if (!job) return sendJson(res, 404, { error: "Job no encontrado" });

    if (action === "edit") {
      const b = await readBody(req);
      if (typeof b.new_title === "string") job.new_title = b.new_title;
      if (typeof b.subjects_and_roles === "string") job.subjects_and_roles = b.subjects_and_roles;
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
        applyProposalToJob(job, out.proposal || {});
        return sendJson(res, 200, { ok: true });
      } catch (e) {
        return sendJson(res, 500, { error: e.message });
      }
    }

    if (action === "regenerate") {
      const b = await readBody(req);
      const panelIdx = parseInt(b.panel || "0", 10);
      const args = ["regenerate", "--job-id", jobId, "--panel", String(panelIdx)];
      if (b.prompt) args.push("--prompt", b.prompt);
      if (typeof b.timestamp === "number" && b.timestamp >= 0) {
        args.push("--timestamp", String(b.timestamp));
      }
      try {
        const out = await runPythonCli(args);
        applyProposalToJob(job, out.proposal || {});
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
    const ext = path.extname(safePath).toLowerCase();
    const contentType =
      ext === ".jpg" || ext === ".jpeg"
        ? "image/jpeg"
        : ext === ".png"
        ? "image/png"
        : "video/mp4";

    if (contentType === "video/mp4" && req.headers.range) {
      const parts = req.headers.range.replace(/bytes=/, "").split("-");
      const start = parseInt(parts[0], 10);
      const end = parts[1] ? parseInt(parts[1], 10) : stat.size - 1;
      res.writeHead(206, {
        "Content-Range": `bytes ${start}-${end}/${stat.size}`,
        "Accept-Ranges": "bytes",
        "Content-Length": end - start + 1,
        "Content-Type": contentType,
      });
      return fs.createReadStream(safePath, { start, end }).pipe(res);
    }
    res.writeHead(200, {
      "Content-Length": stat.size,
      "Content-Type": contentType,
      "Cache-Control": "no-cache",
    });
    return fs.createReadStream(safePath).pipe(res);
  }

  res.writeHead(404);
  res.end("Not found");
});

server.listen(PORT, "127.0.0.1", () => {
  const url = `http://127.0.0.1:${PORT}`;
  console.log(`🔥 AI Manga / Aura Impact Studio corriendo en ${url}`);
  if (!process.argv.includes("--no-open")) {
    spawn("cmd", ["/c", "start", "msedge", `--app=${url}`], {
      detached: true,
      stdio: "ignore",
    }).unref();
  }
  pollTelegram();
});
