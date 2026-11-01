use axum::{
    extract::{Path as AxumPath, State},
    http::StatusCode,
    response::{Html, IntoResponse, Json},
    routing::{get, post},
    Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    env,
    fs,
    net::SocketAddr,
    path::PathBuf,
    process::Stdio,
    sync::Arc,
    time::Duration,
};
use tokio::{process::Command, sync::RwLock, time::sleep};
use tower_http::services::ServeDir;
use uuid::Uuid;

const INDEX_HTML: &str = include_str!("../ui/index.html");

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct SceneItem {
    pub visual: String,
    pub narration: String,
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct JobRecord {
    pub job_id: String,
    pub url: String,
    pub platform: String,
    pub title: String,
    pub status: String, // "downloading" | "analyzing" | "awaiting_approval" | "generating" | "completed" | "error" | "cancelled"
    pub error: Option<String>,
    pub source_video_url: Option<String>,
    pub final_video_url: Option<String>,
    pub engine_used: Option<String>,
    pub original_analysis: String,
    pub new_title: String,
    pub voiceover_script: String,
    pub visual_prompt: String,
    pub scenes: Vec<SceneItem>,
    pub social_caption: String,
    pub chat_id: Option<i64>,
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct AppConfig {
    pub telegram_bot_token: String,
    pub allowed_telegram_user_id: String,
    pub gemini_api_key: String,
    pub gemini_analysis_model: String,
    pub veo_model: String,
    pub tts_provider: String,
    pub edge_tts_voice: String,
    pub elevenlabs_api_key: String,
    pub elevenlabs_voice_id: String,
    pub default_content_style: String,
}

pub struct AppState {
    pub jobs: HashMap<String, JobRecord>,
    pub job_order: Vec<String>,
    pub config: AppConfig,
    pub telegram_connected: bool,
}

type SharedState = Arc<RwLock<AppState>>;

#[tokio::main]
async fn main() {
    let _ = dotenvy::dotenv();
    fs::create_dir_all("workspace/downloads").ok();
    fs::create_dir_all("workspace/outputs").ok();

    let cfg = load_config_from_env();
    let state = Arc::new(RwLock::new(AppState {
        jobs: HashMap::new(),
        job_order: Vec::new(),
        config: cfg,
        telegram_connected: false,
    }));

    // Iniciar loop ligero de Telegram en segundo plano
    let tg_state = state.clone();
    tokio::spawn(async move {
        telegram_polling_loop(tg_state).await;
    });

    let app = Router::new()
        .route("/", get(serve_ui))
        .route("/api/state", get(get_state))
        .route("/api/config", post(save_config))
        .route("/api/analyze", post(start_analyze))
        .route("/api/jobs/:job_id/edit", post(edit_job))
        .route("/api/jobs/:job_id/refine", post(refine_job))
        .route("/api/jobs/:job_id/approve", post(approve_job))
        .route("/api/jobs/:job_id/cancel", post(cancel_job))
        .nest_service("/media", ServeDir::new("workspace"))
        .with_state(state);

    let addr = SocketAddr::from(([127, 0, 0, 1], 8765));
    println!("⚡ Servidor Rust ultra-ligero escuchando en http://{}", addr);

    // Abrir ventana ligera estilo App en Windows
    let _ = Command::new("cmd")
        .args(["/C", "start", "msedge", "--app=http://127.0.0.1:8765"])
        .spawn();

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

fn load_config_from_env() -> AppConfig {
    AppConfig {
        telegram_bot_token: env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default(),
        allowed_telegram_user_id: env::var("ALLOWED_TELEGRAM_USER_ID").unwrap_or_default(),
        gemini_api_key: env::var("GEMINI_API_KEY").unwrap_or_default(),
        gemini_analysis_model: env::var("GEMINI_ANALYSIS_MODEL")
            .unwrap_or_else(|_| "gemini-2.5-flash".to_string()),
        veo_model: env::var("VEO_MODEL").unwrap_or_else(|_| "veo-2.0-generate-001".to_string()),
        tts_provider: env::var("TTS_PROVIDER").unwrap_or_else(|_| "edge".to_string()),
        edge_tts_voice: env::var("EDGE_TTS_VOICE")
            .unwrap_or_else(|_| "es-MX-DaliaNeural".to_string()),
        elevenlabs_api_key: env::var("ELEVENLABS_API_KEY").unwrap_or_default(),
        elevenlabs_voice_id: env::var("ELEVENLABS_VOICE_ID").unwrap_or_default(),
        default_content_style: env::var("DEFAULT_CONTENT_STYLE").unwrap_or_else(|_| {
            "Video corto dinámico (Reel/Short/TikTok) en español, con gancho fuerte en los primeros 3 segundos.".to_string()
        }),
    }
}

async fn serve_ui() -> Html<&'static str> {
    Html(INDEX_HTML)
}

async fn get_state(State(state): State<SharedState>) -> Json<Value> {
    let st = state.read().await;
    let list: Vec<JobRecord> = st
        .job_order
        .iter()
        .rev()
        .filter_map(|id| st.jobs.get(id).cloned())
        .collect();
    Json(json!({
        "jobs": list,
        "telegram_connected": st.telegram_connected,
        "config": {
            "has_telegram_token": !st.config.telegram_bot_token.is_empty(),
            "has_gemini_key": !st.config.gemini_api_key.is_empty(),
            "allowed_telegram_user_id": st.config.allowed_telegram_user_id,
            "gemini_analysis_model": st.config.gemini_analysis_model,
            "veo_model": st.config.veo_model,
            "tts_provider": st.config.tts_provider,
            "edge_tts_voice": st.config.edge_tts_voice,
            "default_content_style": st.config.default_content_style,
        }
    }))
}

#[derive(Deserialize)]
struct ConfigUpdatePayload {
    telegram_bot_token: Option<String>,
    allowed_telegram_user_id: Option<String>,
    gemini_api_key: Option<String>,
    tts_provider: Option<String>,
    edge_tts_voice: Option<String>,
    elevenlabs_api_key: Option<String>,
    elevenlabs_voice_id: Option<String>,
    default_content_style: Option<String>,
}

async fn save_config(
    State(state): State<SharedState>,
    Json(payload): Json<ConfigUpdatePayload>,
) -> Json<Value> {
    let mut st = state.write().await;
    if let Some(v) = payload.telegram_bot_token {
        if !v.trim().is_empty() {
            st.config.telegram_bot_token = v.trim().to_string();
        }
    }
    if let Some(v) = payload.allowed_telegram_user_id {
        st.config.allowed_telegram_user_id = v.trim().to_string();
    }
    if let Some(v) = payload.gemini_api_key {
        if !v.trim().is_empty() {
            st.config.gemini_api_key = v.trim().to_string();
        }
    }
    if let Some(v) = payload.tts_provider {
        st.config.tts_provider = v.trim().to_string();
    }
    if let Some(v) = payload.edge_tts_voice {
        st.config.edge_tts_voice = v.trim().to_string();
    }
    if let Some(v) = payload.elevenlabs_api_key {
        if !v.trim().is_empty() {
            st.config.elevenlabs_api_key = v.trim().to_string();
        }
    }
    if let Some(v) = payload.elevenlabs_voice_id {
        st.config.elevenlabs_voice_id = v.trim().to_string();
    }
    if let Some(v) = payload.default_content_style {
        st.config.default_content_style = v.trim().to_string();
    }

    let env_content = format!(
        "TELEGRAM_BOT_TOKEN={}\nALLOWED_TELEGRAM_USER_ID={}\nGEMINI_API_KEY={}\nGEMINI_ANALYSIS_MODEL={}\nVEO_MODEL={}\nTTS_PROVIDER={}\nEDGE_TTS_VOICE={}\nELEVENLABS_API_KEY={}\nELEVENLABS_VOICE_ID={}\nDEFAULT_CONTENT_STYLE={}\n",
        st.config.telegram_bot_token,
        st.config.allowed_telegram_user_id,
        st.config.gemini_api_key,
        st.config.gemini_analysis_model,
        st.config.veo_model,
        st.config.tts_provider,
        st.config.edge_tts_voice,
        st.config.elevenlabs_api_key,
        st.config.elevenlabs_voice_id,
        st.config.default_content_style.replace('\n', " ")
    );
    let _ = fs::write(".env", env_content);
    Json(json!({ "ok": true }))
}

#[derive(Deserialize)]
struct AnalyzeRequest {
    url: String,
    style: Option<String>,
}

async fn start_analyze(
    State(state): State<SharedState>,
    Json(req): Json<AnalyzeRequest>,
) -> impl IntoResponse {
    let job_id = Uuid::new_v4().to_string()[..8].to_string();
    let style = {
        let st = state.read().await;
        req.style
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| st.config.default_content_style.clone())
    };

    let job = JobRecord {
        job_id: job_id.clone(),
        url: req.url.clone(),
        platform: detect_platform(&req.url),
        title: "Descargando video...".to_string(),
        status: "downloading".to_string(),
        error: None,
        source_video_url: None,
        final_video_url: None,
        engine_used: None,
        original_analysis: String::new(),
        new_title: String::new(),
        voiceover_script: String::new(),
        visual_prompt: String::new(),
        scenes: Vec::new(),
        social_caption: String::new(),
        chat_id: None,
    };

    {
        let mut st = state.write().await;
        st.job_order.push(job_id.clone());
        st.jobs.insert(job_id.clone(), job);
    }

    let bg_state = state.clone();
    let bg_job_id = job_id.clone();
    let url = req.url;
    tokio::spawn(async move {
        run_analyze_pipeline(bg_state, bg_job_id, url, style, None).await;
    });

    (StatusCode::ACCEPTED, Json(json!({ "job_id": job_id })))
}

#[derive(Deserialize)]
struct EditPayload {
    new_title: Option<String>,
    voiceover_script: String,
    visual_prompt: String,
    social_caption: Option<String>,
}

async fn edit_job(
    State(state): State<SharedState>,
    AxumPath(job_id): AxumPath<String>,
    Json(payload): Json<EditPayload>,
) -> impl IntoResponse {
    let mut st = state.write().await;
    if let Some(job) = st.jobs.get_mut(&job_id) {
        if let Some(t) = payload.new_title {
            job.new_title = t;
        }
        job.voiceover_script = payload.voiceover_script;
        job.visual_prompt = payload.visual_prompt;
        if let Some(c) = payload.social_caption {
            job.social_caption = c;
        }
        return (StatusCode::OK, Json(json!({ "ok": true })));
    }
    (StatusCode::NOT_FOUND, Json(json!({ "error": "Job no encontrado" })))
}

#[derive(Deserialize)]
struct RefinePayload {
    feedback: String,
}

async fn refine_job(
    State(state): State<SharedState>,
    AxumPath(job_id): AxumPath<String>,
    Json(payload): Json<RefinePayload>,
) -> impl IntoResponse {
    let output = Command::new("python")
        .args(["cli.py", "refine", "--job-id", &job_id, "--feedback", &payload.feedback])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await;

    if let Ok(out) = output {
        if out.status.success() {
            if let Ok(val) = serde_json::from_slice::<Value>(&out.stdout) {
                let mut st = state.write().await;
                if let Some(job) = st.jobs.get_mut(&job_id) {
                    if let Some(p) = val.get("proposal") {
                        update_job_from_proposal_json(job, p);
                    }
                }
                return (StatusCode::OK, Json(json!({ "ok": true })));
            }
        }
    }
    (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": "No se pudo refinar la propuesta" })))
}

async fn approve_job(
    State(state): State<SharedState>,
    AxumPath(job_id): AxumPath<String>,
) -> impl IntoResponse {
    {
        let mut st = state.write().await;
        if let Some(job) = st.jobs.get_mut(&job_id) {
            job.status = "generating".to_string();
        } else {
            return (StatusCode::NOT_FOUND, Json(json!({ "error": "No encontrado" })));
        }
    }

    let bg_state = state.clone();
    let bg_job_id = job_id.clone();
    tokio::spawn(async move {
        run_generate_pipeline(bg_state, bg_job_id).await;
    });

    (StatusCode::OK, Json(json!({ "ok": true })))
}

async fn cancel_job(
    State(state): State<SharedState>,
    AxumPath(job_id): AxumPath<String>,
) -> impl IntoResponse {
    let mut st = state.write().await;
    if let Some(job) = st.jobs.get_mut(&job_id) {
        job.status = "cancelled".to_string();
        return (StatusCode::OK, Json(json!({ "ok": true })));
    }
    (StatusCode::NOT_FOUND, Json(json!({ "error": "No encontrado" })))
}

fn detect_platform(url: &str) -> String {
    let u = url.to_lowercase();
    if u.contains("youtube.com") || u.contains("youtu.be") {
        "YouTube".to_string()
    } else if u.contains("instagram.com") {
        "Instagram".to_string()
    } else if u.contains("facebook.com") || u.contains("fb.watch") {
        "Facebook".to_string()
    } else if u.contains("tiktok.com") {
        "TikTok".to_string()
    } else {
        "Web".to_string()
    }
}

fn update_job_from_proposal_json(job: &mut JobRecord, p: &Value) {
    if let Some(v) = p.get("original_analysis").and_then(|x| x.as_str()) {
        job.original_analysis = v.to_string();
    }
    if let Some(v) = p.get("new_title").and_then(|x| x.as_str()) {
        job.new_title = v.to_string();
    }
    if let Some(v) = p.get("voiceover_script").and_then(|x| x.as_str()) {
        job.voiceover_script = v.to_string();
    }
    if let Some(v) = p.get("visual_prompt").and_then(|x| x.as_str()) {
        job.visual_prompt = v.to_string();
    }
    if let Some(v) = p.get("social_caption").and_then(|x| x.as_str()) {
        job.social_caption = v.to_string();
    }
}

async fn run_analyze_pipeline(
    state: SharedState,
    job_id: String,
    url: String,
    style: String,
    chat_id: Option<i64>,
) {
    let output = Command::new("python")
        .args(["cli.py", "analyze", "--url", &url, "--style", &style, "--job-id", &job_id])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await;

    match output {
        Ok(out) if out.status.success() => {
            if let Ok(val) = serde_json::from_slice::<Value>(&out.stdout) {
                let mut st = state.write().await;
                if let Some(job) = st.jobs.get_mut(&job_id) {
                    job.status = "awaiting_approval".to_string();
                    job.chat_id = chat_id;
                    if let Some(p) = val.get("proposal") {
                        update_job_from_proposal_json(job, p);
                        job.title = job.new_title.clone();
                    }
                    job.source_video_url = Some(format!("/media/downloads/{}.mp4", job_id));
                }
            }
        }
        Ok(out) => {
            let err_msg = String::from_utf8_lossy(&out.stderr).to_string();
            let mut st = state.write().await;
            if let Some(job) = st.jobs.get_mut(&job_id) {
                job.status = "error".to_string();
                job.error = Some(err_msg);
            }
        }
        Err(e) => {
            let mut st = state.write().await;
            if let Some(job) = st.jobs.get_mut(&job_id) {
                job.status = "error".to_string();
                job.error = Some(e.to_string());
            }
        }
    }
}

async fn run_generate_pipeline(state: SharedState, job_id: String) {
    let (script, prompt) = {
        let st = state.read().await;
        if let Some(j) = st.jobs.get(&job_id) {
            (j.voiceover_script.clone(), j.visual_prompt.clone())
        } else {
            return;
        }
    };

    let output = Command::new("python")
        .args([
            "cli.py",
            "generate",
            "--job-id",
            &job_id,
            "--script",
            &script,
            "--prompt",
            &prompt,
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await;

    match output {
        Ok(out) if out.status.success() => {
            if let Ok(val) = serde_json::from_slice::<Value>(&out.stdout) {
                let mut st = state.write().await;
                if let Some(job) = st.jobs.get_mut(&job_id) {
                    job.status = "completed".to_string();
                    job.final_video_url = Some(format!("/media/outputs/{}_final.mp4", job_id));
                    job.engine_used = val
                        .get("engine_used")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string());
                }
            }
        }
        Ok(out) => {
            let err_msg = String::from_utf8_lossy(&out.stderr).to_string();
            let mut st = state.write().await;
            if let Some(job) = st.jobs.get_mut(&job_id) {
                job.status = "error".to_string();
                job.error = Some(err_msg);
            }
        }
        Err(e) => {
            let mut st = state.write().await;
            if let Some(job) = st.jobs.get_mut(&job_id) {
                job.status = "error".to_string();
                job.error = Some(e.to_string());
            }
        }
    }
}

async fn telegram_polling_loop(state: SharedState) {
    let client = reqwest::Client::new();
    let mut offset: i64 = 0;

    loop {
        let token = { state.read().await.config.telegram_bot_token.clone() };
        if token.is_empty() || token == "tu_token_de_telegram_aqui" {
            sleep(Duration::from_secs(4)).await;
            continue;
        }

        let url = format!(
            "https://api.telegram.org/bot{}/getUpdates?timeout=15&offset={}",
            token, offset
        );
        if let Ok(resp) = client.get(&url).send().await {
            if let Ok(json_body) = resp.json::<Value>().await {
                if json_body.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                    {
                        state.write().await.telegram_connected = true;
                    }
                    if let Some(updates) = json_body.get("result").and_then(|v| v.as_array()) {
                        for u in updates {
                            if let Some(uid) = u.get("update_id").and_then(|v| v.as_i64()) {
                                offset = uid + 1;
                            }
                        }
                    }
                }
            }
        }
        sleep(Duration::from_secs(2)).await;
    }
}
