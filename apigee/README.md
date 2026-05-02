# Jira Dashboard API - Apigee Proxy

Apigee API Proxy configuration for the Jira Dashboard Platform. Provides rate limiting, JWT authentication, CORS, and security headers in front of the FastAPI backend.

## Structure

```
apigee/
├── apiproxy/
│   ├── jira-dashboard-api.xml           # Proxy bundle descriptor
│   ├── proxies/
│   │   └── default.xml                  # Proxy endpoint (PreFlow, Flows, FaultRules)
│   ├── targets/
│   │   ├── default.xml                  # Target endpoint (backend /api/v1 load balancer)
│   │   └── infrastructure.xml           # Target for /health (no /api/v1 prefix)
│   ├── policies/
│   │   ├── KVM-Get-Credentials.xml      # KVM lookup for hostname + JWT secret
│   │   ├── JWT-VerifyAccessToken.xml    # JWT token verification (HS256)
│   │   ├── SA-RateLimit.xml             # Spike Arrest - rate limiting by client IP
│   │   ├── Q-EnforceQuota.xml           # Quota enforcement by JWT user_id
│   │   ├── AM-AddCORSHeaders.xml        # CORS response headers
│   │   ├── AM-AddSecurityHeaders.xml    # Security headers (HSTS, CSP, etc.)
│   │   ├── AM-SetTargetHeaders.xml      # Target request headers (X-Forwarded-*, X-Request-Id)
│   │   ├── RF-CORSPreflight.xml         # CORS OPTIONS preflight handling
│   │   ├── RF-CORSInvalidOrigin.xml     # Reject invalid CORS origins
│   │   ├── JS-ValidateCORSOrigin.xml    # JavaScript CORS origin validation
│   │   ├── JS-ExtractCookieToken.xml    # Extract JWT from httpOnly cookie
│   │   ├── JS-LogResponse.xml           # Response logging / analytics
│   │   ├── AM-InvalidJWTResponse.xml    # 401 error for bad JWT
│   │   ├── AM-RateLimitExceededResponse.xml  # 429 spike arrest error
│   │   ├── AM-QuotaExceededResponse.xml # 429 quota exceeded error
│   │   ├── AM-TargetUnavailableResponse.xml  # 503 backend down error
│   │   ├── AM-DefaultErrorResponse.xml  # 500 catch-all proxy error
│   │   ├── AM-DefaultTargetErrorResponse.xml # 502 target communication error
│   │   └── RF-ResourceNotFound.xml      # 404 not found
│   └── resources/
│       └── jsc/
│           ├── validateCORSOrigin.js     # CORS origin allowlist logic
│           ├── extractCookieToken.js     # Cookie-to-header JWT extraction
│           └── logResponse.js            # Response analytics logging
└── README.md
```

## Security Architecture

This proxy is designed for a **browser-based SPA** (React frontend) that authenticates via **JWT tokens** issued by the EasyLife admin panel.

### Authentication Flow

```
Browser → Apigee (/jira/v1/*) → Backend (/api/v1/*)
   │                                    │
   ├─ GET /health/* ─────────────────► Public (no auth)
   ├─ GET /dashboard/* (with JWT) ──► JWT verified by Apigee ──► Backend
   ├─ POST /sync/trigger (with JWT) ► JWT verified ──► Backend (admin check)
   └─ OPTIONS * ─────────────────────► CORS preflight (no auth)
```

### PreFlow Request Pipeline

Every request passes through these steps in order:

| Step | Policy | Condition | Purpose |
|------|--------|-----------|---------|
| 1 | `JS-ValidateCORSOrigin` | Always | Validate Origin header against allowlist |
| 2 | `RF-CORSPreflight` | OPTIONS + valid origin | Return CORS headers, skip remaining steps |
| 3 | `RF-CORSInvalidOrigin` | OPTIONS + invalid origin | Reject with 403 |
| 4 | `KVM-Get-Credentials` | Non-OPTIONS, non-health | Load hostname + JWT secret from encrypted KVM |
| 5 | `SA-RateLimit` | Non-health | Spike arrest at 100 req/sec per client IP |
| 6 | `JS-ExtractCookieToken` | Protected endpoints | Extract JWT from httpOnly cookie if no Authorization header |
| 7 | `JWT-VerifyAccessToken` | Protected endpoints | Verify HS256 JWT from Authorization header |
| 8 | `Q-EnforceQuota` | Authenticated, non-health | 10,000 req/month per user_id |

## Endpoints

Base path: `/jira/v1`

### Public (no auth required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health/live` | Liveness probe |
| GET | `/health/ready` | Readiness probe |
| OPTIONS | `*` | CORS preflight requests |

### Authenticated (JWT required)

#### Dashboard (`/dashboard`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/dashboard/stats` | Dashboard statistics |
| GET | `/dashboard/issues` | Dashboard issues |
| GET | `/dashboard/canvas` | Dashboard canvas |
| GET | `/dashboard/timeline` | Dashboard timeline |
| GET | `/dashboard/mentions` | Dashboard mentions |
| GET | `/dashboard/boards` | Dashboard boards |
| GET | `/dashboard/blockers` | Dashboard blockers |

#### Issues (`/issues`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/issues/{key}` | Get issue detail |

#### Portfolio (`/portfolio`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolio/capabilities` | Portfolio capabilities |
| GET | `/portfolio/tree` | Portfolio tree view |
| GET | `/portfolio/children` | Portfolio children |
| GET | `/portfolio/snapshots` | Portfolio snapshots |
| GET | `/portfolio/cycle` | Portfolio cycle data |
| GET | `/portfolio/related` | Related portfolio items |
| POST | `/portfolio/exports/portfolio` | Export portfolio as DOCX |

### Admin Only (JWT required + admin role enforced by backend)

#### Sync (`/sync`)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/sync/trigger` | Trigger Jira sync |
| GET | `/sync/progress` | Get sync progress |
| GET | `/sync/config` | Get sync configuration |
| PUT | `/sync/config` | Update sync configuration |
| POST | `/sync/archive` | Archive issues |
| GET | `/sync/archives` | List archives |

#### Issues - Write Operations
| Method | Path | Description |
|--------|------|-------------|
| POST | `/issues/create` | Create a new issue |
| POST | `/issues/{key}/link` | Link issues |
| POST | `/issues/{key}/transition` | Transition an issue |

#### Portfolio - Admin Operations
| Method | Path | Description |
|--------|------|-------------|
| POST | `/portfolio/recompute` | Recompute portfolio rollups |
| POST | `/portfolio/snapshots/run` | Take a portfolio snapshot |

## JWT Verification

| Claim | Flow Variable | Purpose |
|-------|---------------|---------|
| `user_id` | `jwt.user_id` | Identifies the authenticated user |
| `email` | `jwt.email` | User's email address |
| `roles` | `jwt.roles` (array) | User's roles for authorization |

The policy rejects requests (401) if:
1. No Authorization header present
2. Signature doesn't match the secret key
3. Token is expired (beyond 60s grace)
4. `sub` is not `access_token`
5. `iss` is not `easylife-auth`
6. `aud` is not `easylife-api`
7. Required claims (`user_id`, `email`, `roles`) are missing

## Response Security Headers

Applied to all responses via PostFlow:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy: default-src 'self'`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `X-Request-Id: {messageid}`

## Deployment

### Prerequisites

1. Apigee Edge or Apigee X account
2. `apigeecli` or Apigee Management API access
3. Target servers configured in Apigee

### 1. Configure Target Servers

```bash
# Create backend target servers
apigeecli targetservers create \
  --name jira-backend-1 \
  --host your-jira-host-1.com \
  --port 443 \
  --ssl true \
  --org YOUR_ORG \
  --env YOUR_ENV

apigeecli targetservers create \
  --name jira-backend-2 \
  --host your-jira-host-2.com \
  --port 443 \
  --ssl true \
  --org YOUR_ORG \
  --env YOUR_ENV
```

### 2. Configure KVM (Key-Value Map)

Store the backend hostname and JWT secret in an encrypted KVM:

```bash
# Create the encrypted KVM
apigeecli kvms create --name jira-proxykvm --encrypted true --org YOUR_ORG --env YOUR_ENV

# Store the backend hostname
apigeecli kvms entries create \
  --map jira-proxykvm \
  --key hostname \
  --value "your-jira-hostname.com" \
  --org YOUR_ORG \
  --env YOUR_ENV

# Store the JWT secret (MUST match backend AUTH_SECRET_KEY)
apigeecli kvms entries create \
  --map jira-proxykvm \
  --key secretKey \
  --value "your-jwt-secret-key" \
  --org YOUR_ORG \
  --env YOUR_ENV
```

> The JWT secret (`secretKey`) must match exactly the secret key used by the EasyLife auth backend to sign tokens.

### 3. Deploy the Proxy

```bash
# Package and deploy
cd jira-api/apigee/apiproxy
zip -r ../jira-dashboard-api.zip .

apigeecli apis create \
  --name jira-dashboard-api \
  --file ../jira-dashboard-api.zip \
  --org YOUR_ORG

apigeecli apis deploy \
  --name jira-dashboard-api \
  --rev 1 \
  --env YOUR_ENV \
  --org YOUR_ORG
```

Or using the Management API:

```bash
curl -X POST \
  "https://apigee.googleapis.com/v1/organizations/YOUR_ORG/apis?action=import&name=jira-dashboard-api" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@jira-dashboard-api.zip"
```

### 4. Updating After Changes

```bash
cd jira-api/apigee/apiproxy
zip -r ../jira-dashboard-api.zip .

apigeecli apis update \
  --name jira-dashboard-api \
  --file ../jira-dashboard-api.zip \
  --org YOUR_ORG

apigeecli apis deploy \
  --name jira-dashboard-api \
  --rev LATEST \
  --env YOUR_ENV \
  --org YOUR_ORG
```

## Error Responses

All errors follow a consistent JSON format:

```json
{
  "error": "Error Type",
  "message": "Human-readable description",
  "code": "ERROR_CODE",
  "status": 401,
  "timestamp": "2026-04-30T12:00:00.000Z",
  "requestId": "abc-123-def-456"
}
```

| Code | Status | Trigger |
|------|--------|---------|
| `INVALID_TOKEN` | 401 | Missing, expired, or invalid JWT token |
| `QUOTA_EXCEEDED` | 429 | Monthly quota limit reached |
| `RATE_LIMIT_EXCEEDED` | 429 | Spike arrest triggered |
| `SERVICE_UNAVAILABLE` | 503 | Backend unreachable |
| `BAD_GATEWAY` | 502 | Error communicating with backend |
| `INTERNAL_ERROR` | 500 | Unexpected proxy error |
| `RESOURCE_NOT_FOUND` | 404 | Unknown route |

## Target Configuration

The target endpoint uses a **RoundRobin** load balancer with two backend servers:

- Algorithm: RoundRobin (alternates requests evenly)
- Path rewrite: Apigee base `/jira/v1` maps to backend `/api/v1`
- Connection timeout: 30s
- I/O timeout: 60s
- Keepalive: 60s
- Max failures before circuit break: 3
- Retry enabled: yes
