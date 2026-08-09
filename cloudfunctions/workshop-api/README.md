# workshop-api - 创意工坊云端后端（CloudBase HTTP 云函数）

FinHack Pro 创意工坊的云端后端。提供策略市场的 REST API：
浏览 / 搜索 / 上传 / 下载 / 评分评论。

## 架构

```
Python 客户端 (WorkshopCloud)  →  HTTP 云函数 workshop-api  →  CloudBase 资源
                                     │ GET /api/packages            云数据库 workshop_packages
                                     │ POST /api/packages           （元数据 + 评分聚合）
                                     │ GET /api/packages/:id/download
                                     │ POST /api/packages/:id/reviews  云存储 workshop/*.zip
                                     └ 云端网关 /api/workshop 路由
```

## 资源清单

| 资源 | 名称 | 说明 |
|------|------|------|
| 云函数 | `workshop-api` | Nodejs18.15 HTTP 函数，端口 9000 |
| 云数据库集合 | `workshop_packages` | 策略元数据（含 file_id / 评分聚合） |
| 云数据库集合 | `workshop_reviews` | 评分 / 评论 |
| 云存储 | `workshop/` 目录 | 策略包 zip 文件 |
| 网关路由 | `/api/workshop` | WEB_SCF 上游，路径透传关闭 |

## 部署步骤

1. **创建集合**（云开发控制台 → 数据库）：
   - `workshop_packages`、`workshop_reviews`

2. **创建 API Key**（控制台 → 身份验证 → API 密钥管理）：
   - 类型 `api_key`，记为 `CLOUDBASE_APIKEY` 的值

3. **创建云函数**：
   - 运行时 `Nodejs18.15`，类型 `HTTP`
   - 环境变量：`CLOUDBASE_APIKEY=<上面创建的 key>`、`TCB_ENV=<envId>`
   - 部署本目录代码（含 node_modules；HTTP 函数不自动安装依赖）

4. **配置权限**：
   - 函数安全规则：`{"invoke": true}`（允许匿名访问）
   - 数据库集合规则：`{"read": true, "create": true, "update": true, "delete": false}`

5. **网关路由**：
   - 路径 `/api/workshop`，上游 `WEB_SCF` → `workshop-api`
   - 路径透传：**关闭**（网关剥掉前缀，函数收到 `/api/packages`）

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/packages?page=&pageSize=&q=&status=` | 浏览 / 搜索 / 分页 |
| GET | `/api/packages/:id` | 详情 |
| POST | `/api/packages` | 上传（JSON：package_id/name/version/zip_base64/...） |
| GET | `/api/packages/:id/download` | 临时下载 URL（1 小时有效） |
| POST | `/api/packages/:id/reviews` | 评分 / 评论（rating 1-5） |
| GET | `/api/packages/:id/reviews` | 评论列表 |

## 踩坑记录

- **HTTP 函数不自动注入 TCB_ENV**：必须显式配置环境变量，否则 fileID 拼接失败
- **存储 fileID 需完整 `cloud://env.bucket/path`**：相对路径返回 `STORAGE_FILE_NONEXIST`
- **uploadFile 返回的 fileID 最可靠**：上传时直接保存返回值，下载复用
- **getTempFileURL 返回字段是 `download_url`**（非 `tempFileURL`）
- **node-sdk storage 上传/下载挂在 app 上**：`app.uploadFile()` / `app.getTempFileURL()`，无 `app.storage()`
- **HTTP 函数依赖需随代码打包**：本地 `npm install` 后连同 node_modules 一起部署
