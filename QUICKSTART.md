# Quick Start Guide

## TL;DR

Get started with Batocera Overmind in 3 steps:

### 1. Install dependencies
```bash
cd batocera.overmind
make install
# or: python3 -m pip install --user -r requirements.txt
```

### 2. Run the server
```bash
make run
# or: python3 -m uvicorn src.overmind.main:app --reload
```

### 3. Open in browser
- **UI**: [http://localhost:8000](http://localhost:8000)
- **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Features Implemented ✅

### User Management
- [x] User registration (email + password)
- [x] User login with JWT
- [x] Secure password hashing with bcrypt
- [ ] Gmail OAuth (stubbed out for future)

### Device Management
- [x] Device registration endpoint (for batocera.drone)
- [x] Device listing (authenticated)
- [x] Device details view
- [x] Support for Batocera system info capture

### ROM/Game Tracking
- [x] Upload ROM metadata per device
- [x] Group ROMs by system
- [x] Track MD5 hashes and file sizes
- [x] View all ROMs for a device

### Game Play Logging
- [x] Log games played with timestamp
- [x] Track play duration
- [x] View game history by device
- [x] Filter by system

### API Documentation
- [x] OpenAPI/Swagger support (automatic with FastAPI)
- [x] Beautiful interactive API docs at `/docs`

### Web UI
- [x] Beautiful, responsive web interface
- [x] User registration flow
- [x] Login/logout
- [x] Device dashboard
- [x] ROM browser
- [x] Game play history viewer

---

## Running with Different Methods

### Using Make (Recommended)
```bash
make run        # Run development server with reload
make test       # Run tests
make format     # Format code
make lint       # Check code quality
make docker-run # Run with Docker Compose
```

### Using pip
```bash
python3 -m pip install --user -r requirements.txt
python3 -m uvicorn src.overmind.main:app --reload
```

### Using Docker
```bash
docker build -t batocera-overmind .
docker run -p 8000:8000 batocera-overmind
```

### Using Docker Compose
```bash
docker-compose up
```

### Using the bash script
```bash
./run.sh
```

---

## Testing

### Run all tests
```bash
make test
```

### Run specific test
```bash
pytest tests/test_api.py::test_register_user -v
```

---

## API Examples

### Register a user
```bash
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123",
    "full_name": "John Doe"
  }'
```

### Login
```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123"
  }'
# Returns: { "access_token": "...", "token_type": "bearer" }
```

### Register a device
```bash
curl -X POST "http://localhost:8000/api/devices/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123",
    "device_id": "device-uuid-123",
    "device_name": "My Cabinet",
    "batocera_info": {
      "model": "Batocera DevBox",
      "system": "Linux 6.6.0",
      "architecture": "x86_64",
      "cpu_model": "AMD Ryzen 7 7800X3D",
      "cpu_cores": 8,
      "cpu_threads": 16,
      "cpu_max_frequency": "5.00 GHz",
      "temperature": "51 C",
      "memory_available": "25.4 GiB",
      "memory_total": "32 GiB",
      "display_resolution": "1920x1080",
      "display_refresh_rate": "60 Hz",
      "data_partition_available": "812 GiB",
      "ip_address": "192.168.1.123",
      "battery": "N/A"
    }
  }'
```

### List devices
```bash
curl -X GET "http://localhost:8000/api/devices" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Upload ROMs
```bash
curl -X POST "http://localhost:8000/api/devices/device-uuid-123/roms" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device-uuid-123",
    "system_name": "snes",
    "roms": [
      {
        "rom_name": "Super Mario Bros",
        "rom_md5": "aabbccdd112233445566778899aabbcc",
        "file_path": "/roms/snes/Super Mario Bros.zip",
        "file_size": 409600
      }
    ]
  }'
```

### Log game play
```bash
curl -X POST "http://localhost:8000/api/devices/device-uuid-123/gameplay" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device-uuid-123",
    "system_name": "snes",
    "game_name": "Super Mario Bros",
    "duration_seconds": 1800
  }'
```

---

## Project Structure

```
batocera.overmind/
├── src/overmind/
│   ├── __init__.py           # Package init
│   ├── main.py               # FastAPI app + routes
│   ├── models.py             # Pydantic models
│   ├── db.py                 # In-memory database
│   └── auth.py               # JWT auth utilities
├── tests/
│   ├── __init__.py
│   └── test_api.py           # API tests
├── Dockerfile                # Docker image definition
├── docker-compose.yml        # Docker Compose config
├── Makefile                  # Make commands
├── pyproject.toml            # Project configuration
├── README.md                 # Full documentation
├── QUICKSTART.md             # This file
├── requirements.txt          # Python dependencies
├── run.sh                    # Shell startup script
└── .env.example              # Example environment variables
```

---

## Next Steps

### For Development
1. Modify the code in `src/overmind/`
2. Server auto-reloads with `--reload` flag
3. Check API docs at `/docs` while developing

### For Production
See [README.md](README.md#security-notes) for security checklist

### Adding Features
1. Add models to `src/overmind/models.py`
2. Add database logic to `src/overmind/db.py`
3. Add routes to `src/overmind/main.py`
4. Add tests to `tests/test_api.py`

### Database Migration
The project currently uses in-memory storage. To switch to a real database:
1. Install SQLAlchemy: `pip install sqlalchemy`
2. Replace `FakeDatabase` in `db.py` with SQLAlchemy ORM
3. Update connection strings as needed

---

## Troubleshooting

### Port 8000 already in use
```bash
python3 -m uvicorn src.overmind.main:app --reload --port 8001
```

### Import errors
```bash
# Reinstall in development mode
python3 -m pip install --user -r requirements.txt
python3 -m pip install --user pytest pytest-asyncio httpx ruff black mypy
```

### Tests failing
```bash
# Reinstall test dependencies
python3 -m pip install --user -r requirements.txt
python3 -m pip install --user pytest pytest-asyncio httpx ruff black mypy
make test
```

---

## Support

- 📖 **Full Documentation**: See [README.md](README.md)
- 🐛 **Issues**: GitHub Issues
- 💬 **Questions**: Feel free to ask!

---

Happy coding! 🎮
