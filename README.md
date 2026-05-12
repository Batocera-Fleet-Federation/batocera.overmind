# Batocera Overmind

**Batocera Overmind** is an integration and automation service for Batocera systems.  
A comprehensive system management and game tracking API for Batocera gaming systems. This application allows users to register their Batocera devices, track games played, and manage their game collections.

**Main features:**

- **Centralized Management:** Connects multiple Batocera devices for fleet management.
- **Remote Actions:** Send commands and manage devices from a central dashboard.
- **Automation:** Schedule and automate tasks like updates, syncs, or maintenance.
- **Integration with Drone:** Works together with Batocera Drone for enhanced admin and monitoring capabilities.
- **Action Logging:** Tracks all actions and device responses for easy troubleshooting.
- **Secure Communication:** Uses authentication and secure connections for device management.
- **Extensible:** Designed to support future integrations and custom workflows.

## Features

- **User Management**
  - User registration with email and password
  - Secure login with JWT authentication
  - Stub for Gmail OAuth login (future feature)

- **Device Management**
  - Register Batocera devices with detailed system information
  - Track device capabilities (CPU, memory, display, etc.)
  - View all registered devices and their status

- **ROM/Game Tracking**
  - Upload ROM metadata (system, game name, MD5 hash)
  - Organize ROMs by system and device
  - Track file sizes and paths

- **Game Play Logging**
  - Log games played with timestamps
  - Track play duration
  - View gaming history by device and system

- **Interactive UI**
  - Beautiful web interface for device and game management
  - Real-time device status updates
  - Gaming history visualization

- **API Documentation**
  - Built-in OpenAPI/Swagger documentation
  - Comprehensive endpoint documentation

## Project Structure

```
batocera.overmind/
├── app/
│   └── main.py                  # Script-style entrypoint (like batocera.drone)
├── scripts/
│   └── run_now.sh               # Quick bootstrap and run helper
├── src/
│   └── overmind/
│       ├── __init__.py           # Package initialization
│       ├── main.py               # FastAPI application
│       ├── models.py             # Pydantic models
│       ├── db.py                 # In-memory database
│       └── auth.py               # Authentication utilities
├── pyproject.toml                # Project configuration
├── README.md                      # This file
└── requirements.txt              # Python dependencies
```

## Installation

### Prerequisites
- Python 3.9+
- pip or poetry

### Setup

1. Clone the repository:
```bash
git clone https://github.com/Batocera-Fleet-Federation/batocera.overmind.git
cd batocera.overmind
```

2. Install dependencies:
```bash
python3 -m pip install --user -r requirements.txt
```

Or with development dependencies:
```bash
python3 -m pip install --user -r requirements.txt
python3 -m pip install --user pytest pytest-asyncio httpx ruff black mypy
```

## Running the Application

Start the development server:

```bash
python3 app/main.py
```

The application will be available at:
- **UI**: `http://localhost:8000`
- **API Docs**: `http://localhost:8000/docs`
- **Alternative API Docs**: `http://localhost:8000/redoc`

## API Endpoints

### Authentication
- `POST /api/auth/register` - Register a new user
- `POST /api/auth/login` - Login and get access token

### Device Management
- `POST /api/devices/register` - Register a new Batocera device
- `GET /api/devices` - List all devices for user
- `GET /api/devices/{device_id}` - Get device details

### ROM Management
- `GET /api/systems` - List systems summary across your devices
- `POST /api/devices/{device_id}/roms` - Upload ROM metadata
- `GET /api/devices/{device_id}/roms` - Get ROMs for device

### Game Play Logging
- `POST /api/devices/{device_id}/gameplay` - Log a game play session
- `GET /api/devices/{device_id}/gamelogs` - Get game play history

### Health
- `GET /health` - Health check endpoint

## Database

Currently, the application uses an in-memory database for development/testing. This means:
- Data is stored in RAM
- Data is lost when the server restarts
- Perfect for development and testing

In production, this should be replaced with a persistent database (PostgreSQL, MongoDB, etc.).

## Batocera Device Registration

The Batocera drone app can register a device by sending:

```bash
curl -X POST "http://localhost:8000/api/devices/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "password123",
    "device_id": "unique-device-uuid",
    "device_name": "My Arcade Cabinet",
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

## Example Usage

### 1. Register a User
```bash
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "securepassword123",
    "full_name": "John Doe"
  }'
```

### 2. Login
```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "securepassword123"
  }'
```

### 3. Register Device (using email/password auth)
```bash
curl -X POST "http://localhost:8000/api/devices/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "securepassword123",
    "device_id": "device-uuid-123",
    "device_name": "Living Room Arcade",
    "batocera_info": {...}
  }'
```

### 4. Upload ROM Metadata
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

### 5. Log Game Play
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

## Future Features

- [ ] Gmail OAuth integration
- [ ] Real database backend (PostgreSQL/MongoDB)
- [ ] WebSocket support for real-time updates
- [ ] Bulk ROM import
- [ ] Gaming statistics and trends
- [ ] Multi-user device sharing
- [ ] Device remote management
- [ ] Game collection validation
- [ ] Achievements system
- [ ] Mobile app support

## Security Notes

⚠️ **IMPORTANT**: This is a development version. Before deploying to production:

1. Change the `SECRET_KEY` in `auth.py`
2. Enable HTTPS
3. Use a real database
4. Implement rate limiting
5. Add CORS restrictions
6. Use environment variables for sensitive data
7. Implement request validation middleware
8. Add comprehensive logging

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see LICENSE file for details

## Support

For issues, questions, or suggestions, please open an issue on GitHub.

---

Made with ❤️ for the Batocera community
