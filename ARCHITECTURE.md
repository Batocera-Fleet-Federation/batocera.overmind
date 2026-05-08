# Batocera Overmind Architecture

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Users' Web Browsers                       │
├─────────────────────────────────────────────────────────────┤
│                    Beautiful Web UI                          │
│  (Registration, Login, Device View, ROM Browser, Logs)      │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP/REST
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                       │
├─────────────────────────────────────────────────────────────┤
│
│  ┌─────────────────────────────────────────────────────┐
│  │            Authentication Routes                    │
│  │  • POST /api/auth/register      - Register user    │
│  │  • POST /api/auth/login         - Login            │
│  └─────────────────────────────────────────────────────┘
│
│  ┌─────────────────────────────────────────────────────┐
│  │            Device Routes                           │
│  │  • POST /api/devices/register   - Register device  │
│  │  • GET /api/devices             - List devices     │
│  │  • GET /api/devices/{id}        - Device details   │
│  └─────────────────────────────────────────────────────┘
│
│  ┌─────────────────────────────────────────────────────┐
│  │            ROM Routes                              │
│  │  • POST /api/devices/{id}/roms  - Upload ROMs      │
│  │  • GET /api/devices/{id}/roms   - Get ROMs         │
│  └─────────────────────────────────────────────────────┘
│
│  ┌─────────────────────────────────────────────────────┐
│  │            Game Play Routes                        │
│  │  • POST /api/devices/{id}/gameplay  - Log play     │
│  │  • GET /api/devices/{id}/gamelogs   - Get history  │
│  └─────────────────────────────────────────────────────┘
│
│  ┌─────────────────────────────────────────────────────┐
│  │            Static Content                          │
│  │  • GET / (root)         - Serve web UI            │
│  │  • GET /docs            - OpenAPI/Swagger docs    │
│  │  • GET /health          - Health check            │
│  └─────────────────────────────────────────────────────┘
│
└────────────────────────┬────────────────────────────────────┘
                         │
                         ↓
        ┌─────────────────────────────────┐
        │     Authentication Layer        │
        ├─────────────────────────────────┤
        │ • JWT token validation          │
        │ • Password hashing (bcrypt)     │
        │ • Bearer token extraction       │
        └────────────┬────────────────────┘
                     │
        ┌────────────↓────────────────────┐
        │      Data Storage Layer         │
        ├─────────────────────────────────┤
        │   FakeDatabase (In-Memory)      │
        │                                 │
        │  • Users table                  │
        │  • Devices table                │
        │  • ROMs table                   │
        │  • GameLogs table               │
        │                                 │
        │  ≡ Future: PostgreSQL/MongoDB ≡ │
        └─────────────────────────────────┘
```

## Data Flow

### User Registration Flow
```
User → Web Form → /api/auth/register → Hash Password → Store in DB → Return User
```

### Device Registration Flow
```
batocera.drone → /api/devices/register (email+pass) → Verify User → Create Device → Store in DB
```

### ROM Upload Flow
```
API Client → /api/devices/{id}/roms → Verify Auth → Store ROM Metadata → Return ROM IDs
```

### Game Play Logging Flow
```
Device → /api/devices/{id}/gameplay → Verify Auth → Log Entry → Update last_seen → Return ID
```

## Database Schema (Current - In-Memory)

```
Users
├── id (UUID)
├── email (unique)
├── password (hashed)
├── full_name (optional)
└── created_at

Devices
├── id (UUID)
├── user_id (FK → Users)
├── device_id (unique per user)
├── device_name
├── batocera_info (JSON)
│   ├── model
│   ├── system
│   ├── architecture
│   ├── cpu_model
│   ├── cpu_cores
│   ├── cpu_threads
│   ├── cpu_max_frequency
│   ├── temperature
│   ├── memory_available
│   ├── memory_total
│   ├── display_resolution
│   ├── display_refresh_rate
│   ├── data_partition_available
│   ├── ip_address
│   └── battery
├── registered_at
└── last_seen

ROMs
├── id (UUID)
├── device_id (FK → Devices)
├── system_name
├── rom_name
├── rom_md5
├── file_path
├── file_size
└── added_at

GameLogs
├── id (UUID)
├── device_id (FK → Devices)
├── system_name
├── game_name
├── played_at
└── duration_seconds
```

## Module Organization

```
src/overmind/
│
├── __init__.py
│   └── Package initialization
│
├── main.py
│   └── FastAPI application
│       ├── Routes (auth, devices, roms, gamelogs)
│       ├── Middleware (CORS)
│       ├── UI HTML serving
│       └── Startup/shutdown events
│
├── models.py
│   └── Pydantic data models
│       ├── User models
│       ├── Device models
│       ├── ROM models
│       ├── GamePlay models
│       └── Request DTOs
│
├── db.py
│   └── Database abstraction
│       ├── FakeDatabase class
│       ├── User operations
│       ├── Device operations
│       ├── ROM operations
│       └── GamePlay operations
│
└── auth.py
    └── Authentication utilities
        ├── Password hashing
        ├── JWT operations
        └── Token validation
```

## Request/Response Flow

### Example: Register Device

**Request:**
```json
POST /api/devices/register
{
  "email": "user@example.com",
  "password": "securepass123",
  "device_id": "device-uuid-123",
  "device_name": "My Cabinet",
  "batocera_info": { ... }
}
```

**Processing:**
1. Validate request body
2. Verify user exists (lookup by email)
3. Verify password (bcrypt compare)
4. Check device not already registered
5. Create device entry in database
6. Generate internal UUID for device
7. Return success response

**Response:**
```json
{
  "message": "Device registered successfully",
  "device": {
    "id": "internal-uuid",
    "device_id": "device-uuid-123",
    "device_name": "My Cabinet",
    "registered_at": "2026-05-07T18:00:00"
  }
}
```

## Security Architecture

```
┌─────────────────────────────────────┐
│      Password Security              │
├─────────────────────────────────────┤
│ • bcrypt hashing (12 salt rounds)   │
│ • Constant-time comparison          │
│ • Min 8 character requirement       │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│      Token Security                 │
├─────────────────────────────────────┤
│ • JWT signed with HS256 algorithm   │
│ • 30-minute expiration              │
│ • Bearer token in Authorization     │
│ • Token validation on protected     │
│   endpoints                         │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│      Transport Security             │
├─────────────────────────────────────┤
│ • CORS enabled (dev: all origins)   │
│ • :arrow_right: Production: restrict │
│ • HTTPS recommended (prod)          │
└─────────────────────────────────────┘
```

## Deployment Architecture (Future)

```
┌──────────────────────────────────────┐
│     Users on Internet                │
└──────────────┬───────────────────────┘
               │
               ↓
     ┌─────────────────────┐
     │  CDN / Load Balancer│
     │  (Optional)         │
     └──────────┬──────────┘
                │
    ┌───────────┴──────────┐
    ↓                      ↓
┌─────────────────┐  ┌─────────────────┐
│ API Instance 1  │  │ API Instance 2   │
├─────────────────┤  ├──────────────────┤
│ FastAPI/Uvicorn │  │ FastAPI/Uvicorn  │
└────────┬────────┘  └────────┬─────────┘
         │                    │
         └──────────┬─────────┘
                    │
                    ↓
         ┌──────────────────────┐
         │  Database            │
         │  (PostgreSQL /       │
         │   MongoDB)           │
         └──────────────────────┘
```

## Technology Stack

```
Framework:       FastAPI (Python web framework)
Async:           AsyncIO / Uvicorn
Validation:      Pydantic (v2)
Auth:            JWT (PyJWT) + bcrypt
API Docs:        OpenAPI 3.0 / Swagger / ReDoc
Frontend:        Vanilla HTML/CSS/JavaScript
Storage:         In-Memory (→ PostgreSQL/MongoDB)
Containerization: Docker + Docker Compose
Package Manager: pip / setuptools
Testing:         pytest + pytest-asyncio
Quality:         black, ruff, mypy
```

## Performance Considerations

Current (Development):
- In-memory database: O(1) lookups
- No persistence across restarts
- Single-threaded async handling
- Suitable for low-traffic testing

Future Production:
- Database indexing on frequently queried fields
- Connection pooling for DB
- Caching layer (Redis) for ROM/gamelog queries
- Pagination for large lists
- Async background tasks for logging
- Horizontal scaling with multiple instances

---

Author: GitHub Copilot
Date: 2026-05-07
