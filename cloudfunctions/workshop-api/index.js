/**
 * FinHack Pro 创意工坊 - HTTP 云函数
 *
 * 提供策略工坊社区后端的 REST API：
 *   GET  /api/packages            列出策略包（分页 + 搜索）
 *   GET  /api/packages/:id        策略包详情
 *   POST /api/packages            上传策略包（manifest + 代码，base64 zip）
 *   POST /api/packages/:id/reviews 评分/评论
 *   GET  /api/packages/:id/reviews 评论列表
 *   GET  /api/packages/:id/download 获取下载临时 URL
 *
 * 技术栈：
 *   - Node.js HTTP Function（监听 9000 端口）
 *   - @cloudbase/node-sdk：云数据库（flexdb 文档型）+ 云存储
 *   - 凭据：CLOUDBASE_APIKEY（服务端 API Key，环境变量注入，不写入代码）
 *
 * 数据模型：
 *   workshop_packages: { _id, package_id, name, version, author, description,
 *                        type, entry, entry_class, params_schema, deps,
 *                        benchmark, file_path, downloads, rating_avg,
 *                        rating_count, created_at, updated_at, review_status }
 *   workshop_reviews:  { _id, package_id, author, rating, comment, created_at }
 */

"use strict";

const http = require("http");
const { URL } = require("url");
const crypto = require("crypto");

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

// CloudBase 客户端延迟初始化：首次请求时才 init，
// 避免顶层初始化失败导致进程崩溃（网关 443）。
// 显式凭据：CLOUDBASE_APIKEY（服务端 API Key，环境变量注入）。
let tcbApp = null;

function getApp() {
  if (tcbApp) return tcbApp;
  const tcb = require("@cloudbase/node-sdk");
  tcbApp = tcb.init({
    env: process.env.TCB_ENV,
    accessKey: process.env.CLOUDBASE_APIKEY,
  });
  return tcbApp;
}

const PACKAGES_COLLECTION = "workshop_packages";
const REVIEWS_COLLECTION = "workshop_reviews";
const STORAGE_PREFIX = "workshop/";

// ============================================================
// 基础工具
// ============================================================

function sendJson(res, statusCode, data) {
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    ...CORS_HEADERS,
  });
  res.end(JSON.stringify(data));
}

function sendOptions(res) {
  res.writeHead(204, CORS_HEADERS);
  res.end();
}

function readBody(req, limitMb = 20) {
  return new Promise((resolve, reject) => {
    let raw = "";
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > limitMb * 1024 * 1024) {
        reject(new Error("body_too_large"));
        req.destroy();
        return;
      }
      raw += chunk;
    });
    req.on("end", () => {
      if (!raw) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (e) {
        reject(new Error("invalid_json"));
      }
    });
    req.on("error", reject);
  });
}

function nowIso() {
  return new Date().toISOString();
}

/**
 * 构造完整 cloud:// fileID。
 * 本地验证过：cloud://{env}.6164-{env}-1463991490/{path} 返回 SUCCESS。
 * AppID 1463991490 为该环境固定值（TCB_STORAGE_BUCKET 存在时优先使用）。
 */
function buildCloudFileId(cloudPath) {
  const envId = process.env.TCB_ENV || process.env.TCB_ENVID || "";
  const bucket = process.env.TCB_STORAGE_BUCKET || `6164-${envId}-1463991490`;
  const p = String(cloudPath || "").replace(/^cloud:\/\/[^/]+\//, "");
  return `cloud://${envId}.${bucket}/${p}`;
}

function safeId(input) {
  return String(input || "").replace(/[^a-zA-Z0-9_\-.]/g, "_").slice(0, 80);
}

// ============================================================
// 业务逻辑
// ============================================================

/** 列出策略包 */
async function listPackages(query) {
  const page = Math.max(parseInt(query.get("page") || "1", 10), 1);
  const pageSize = Math.min(Math.max(parseInt(query.get("pageSize") || "20", 10), 1), 50);
  const keyword = (query.get("q") || "").trim();
  const status = (query.get("status") || "").trim();
  const db = getApp().database();
  const _ = db.command;

  let condition = {};
  if (status) {
    condition.review_status = status;
  }

  // 关键词：匹配 name / id / author
  const coll = db.collection(PACKAGES_COLLECTION);
  let countQuery = coll.where(condition).count();
  let count = 0;
  if (!keyword) {
    const c = await countQuery;
    count = c.total || 0;
  }

  let snapshot;
  if (keyword) {
    // 简化搜索：优先在 id 上精确匹配，其次按 name 模糊（用正则）
    snapshot = await coll
      .where(
        _.or([
          { package_id: _.eq(keyword) },
          { name: db.RegExp({ regexp: escapeRegExp(keyword), options: "i" }) },
          { author: db.RegExp({ regexp: escapeRegExp(keyword), options: "i" }) },
        ])
      )
      .orderBy("created_at", "desc")
      .limit(pageSize)
      .skip((page - 1) * pageSize)
      .get();
  } else {
    snapshot = await coll
      .where(condition)
      .orderBy("created_at", "desc")
      .limit(pageSize)
      .skip((page - 1) * pageSize)
      .get();
  }

  const items = (snapshot.data || []).map((doc) => ({
    package_id: doc.package_id,
    name: doc.name,
    version: doc.version,
    author: doc.author,
    description: doc.description,
    type: doc.type,
    entry_class: doc.entry_class,
    params_schema: doc.params_schema,
    downloads: doc.downloads || 0,
    rating_avg: doc.rating_avg || 0,
    rating_count: doc.rating_count || 0,
    created_at: doc.created_at,
    updated_at: doc.updated_at,
    review_status: doc.review_status || "pending",
  }));

  return { items, page, pageSize, total: count || items.length };
}

function escapeRegExp(str) {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 策略包详情 */
async function getPackage(packageId) {
  const db = getApp().database();
  const res = await db
    .collection(PACKAGES_COLLECTION)
    .where({ package_id: packageId })
    .limit(1)
    .get();
  const doc = res.data && res.data[0];
  if (!doc) return null;
  return doc;
}

/** 上传策略包（zip 经 base64 传入，存云存储 + 元数据入库） */
async function createPackage(body) {
  const required = ["package_id", "name", "version"];
  for (const field of required) {
    if (!body[field]) {
      const err = new Error(`missing_field: ${field}`);
      err.status = 400;
      throw err;
    }
  }

  const packageId = safeId(body.package_id);
  const version = String(body.version || "1.0.0").replace(/[^\d.]/g, "").slice(0, 20);
  const zipB64 = body.zip_base64 || "";

  // 去重：同 package_id + version 已存在则拒绝
  const existing = await getPackage(packageId);
  if (existing && existing.version === version) {
    const err = new Error(`already_exists: ${packageId}@${version}`);
    err.status = 409;
    throw err;
  }

  const app = getApp();
  const db = app.database();

  let filePath = "";
  let fileId = "";
  if (zipB64) {
    const buffer = Buffer.from(zipB64, "base64");
    filePath = `${STORAGE_PREFIX}${packageId}-v${version}.zip`;
    const upRes = await app.uploadFile({
      cloudPath: filePath,
      fileContent: buffer,
    });
    // 优先用 uploadFile 返回值；为空则用固定 bucket 拼法兜底
    fileId = (upRes && upRes.fileID) || buildCloudFileId(filePath);
  }

  const now = nowIso();
  const doc = {
    package_id: packageId,
    name: String(body.name || packageId),
    version,
    author: String(body.author || "anonymous").slice(0, 64),
    description: String(body.description || "").slice(0, 2000),
    type: String(body.type || "strategy").slice(0, 32),
    entry: String(body.entry || "strategy.py").slice(0, 128),
    entry_class: String(body.entry_class || "").slice(0, 128),
    params_schema: body.params_schema || {},
    deps: Array.isArray(body.deps) ? body.deps.slice(0, 50) : [],
    benchmark: body.benchmark || {},
    preview: String(body.preview || "").slice(0, 512),
    file_path: filePath,
    file_id: fileId,
    downloads: 0,
    rating_avg: 0,
    rating_count: 0,
    review_status: String(body.review_status || "pending").slice(0, 16),
    created_at: now,
    updated_at: now,
  };

  const addRes = await db.collection(PACKAGES_COLLECTION).add(doc);
  return { _id: addRes.id, ...doc };
}

/** 评分/评论 */
async function addReview(packageId, body) {
  const rating = Math.min(Math.max(parseInt(body.rating || "5", 10), 1), 5);
  const comment = String(body.comment || "").slice(0, 1000);
  const author = String(body.author || "anonymous").slice(0, 64);
  const db = getApp().database();

  const pkg = await getPackage(packageId);
  if (!pkg) {
    const err = new Error("not_found");
    err.status = 404;
    throw err;
  }

  const now = nowIso();
  const review = {
    package_id: packageId,
    author,
    rating,
    comment,
    created_at: now,
  };
  await db.collection(REVIEWS_COLLECTION).add(review);

  // 更新聚合评分
  const newCount = (pkg.rating_count || 0) + 1;
  const newAvg = ((pkg.rating_avg || 0) * (pkg.rating_count || 0) + rating) / newCount;
  await db
    .collection(PACKAGES_COLLECTION)
    .where({ package_id: packageId })
    .update({
      rating_avg: Math.round(newAvg * 100) / 100,
      rating_count: newCount,
      updated_at: now,
    });

  return { package_id: packageId, rating, comment, author, created_at: now };
}

/** 评论列表 */
async function listReviews(packageId) {
  const db = getApp().database();
  const res = await db
    .collection(REVIEWS_COLLECTION)
    .where({ package_id: packageId })
    .orderBy("created_at", "desc")
    .limit(50)
    .get();
  return (res.data || []).map((d) => ({
    author: d.author,
    rating: d.rating,
    comment: d.comment,
    created_at: d.created_at,
  }));
}

/** 下载链接（临时 URL，1 小时有效） */
async function getDownloadUrl(packageId) {
  const app = getApp();
  const db = app.database();
  const _ = db.command;
  const pkg = await getPackage(packageId);
  if (!pkg) {
    const err = new Error("not_found");
    err.status = 404;
    throw err;
  }
  if (!pkg.file_path) {
    const err = new Error("no_file");
    err.status = 404;
    throw err;
  }

  // 累加下载计数
  await db
    .collection(PACKAGES_COLLECTION)
    .where({ package_id: packageId })
    .update({ downloads: _.inc(1) });

  // 生成临时下载 URL（优先用上传时存的完整 file_id，否则动态拼接）
  let url = "";
  try {
    const cloudFileId = pkg.file_id || buildCloudFileId(pkg.file_path);
    const fileRes = await app.getTempFileURL({
      fileList: [{ fileID: cloudFileId, maxAge: 3600 }],
    });
    const first = fileRes && fileRes.fileList && fileRes.fileList[0];
    if (first && first.code === "SUCCESS") {
      url = first.download_url || first.tempFileURL || "";
    }
  } catch (e) {
    console.error("getTempFileURL failed:", e && e.message ? e.message : String(e));
  }
  return { package_id: packageId, url, file_path: pkg.file_path };
}

// ============================================================
// 路由
// ============================================================

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    return sendOptions(res);
  }

  try {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    const path = url.pathname.replace(/\/+$/, "") || "/";

    // GET /api/packages
    if (req.method === "GET" && path === "/api/packages") {
      const data = await listPackages(url.searchParams);
      return sendJson(res, 200, { success: true, data });
    }

    // POST /api/packages
    if (req.method === "POST" && path === "/api/packages") {
      const body = await readBody(req, 25);
      const data = await createPackage(body);
      return sendJson(res, 201, { success: true, data });
    }

    // GET /api/packages/:id
    const detailMatch = path.match(/^\/api\/packages\/([^/]+)$/);
    if (req.method === "GET" && detailMatch) {
      const pkg = await getPackage(decodeURIComponent(detailMatch[1]));
      if (!pkg) return sendJson(res, 404, { success: false, error: "not_found" });
      return sendJson(res, 200, { success: true, data: pkg });
    }

    // GET /api/packages/:id/download
    const downloadMatch = path.match(/^\/api\/packages\/([^/]+)\/download$/);
    if (req.method === "GET" && downloadMatch) {
      const data = await getDownloadUrl(decodeURIComponent(downloadMatch[1]));
      return sendJson(res, 200, { success: true, data });
    }

    // POST /api/packages/:id/reviews
    const reviewMatch = path.match(/^\/api\/packages\/([^/]+)\/reviews$/);
    if (req.method === "POST" && reviewMatch) {
      const body = await readBody(req, 2);
      const data = await addReview(decodeURIComponent(reviewMatch[1]), body);
      return sendJson(res, 201, { success: true, data });
    }

    // GET /api/packages/:id/reviews
    if (req.method === "GET" && reviewMatch) {
      const data = await listReviews(decodeURIComponent(reviewMatch[1]));
      return sendJson(res, 200, { success: true, data });
    }

    // GET /health
    if (req.method === "GET" && path === "/health") {
      return sendJson(res, 200, { success: true, message: "workshop-api ok" });
    }

    return sendJson(res, 404, { success: false, error: "not_found" });
  } catch (e) {
    const status = e.status || (e.message === "invalid_json" || e.message === "body_too_large" ? 400 : 500);
    if (status >= 500) {
      console.error("workshop-api error:", e);
    }
    return sendJson(res, status, { success: false, error: e.message || "internal_error" });
  }
});

server.listen(9000, () => {
  console.log("workshop-api listening on 9000");
});
