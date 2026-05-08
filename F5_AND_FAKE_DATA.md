# VS Code F5 & Fake Data Setup Guide

## What Was Added

### 1. VS Code Configuration (`.vscode/` directory)

#### `launch.json` - Run with F5
- Configured Python debug launcher
- Starts FastAPI development server with `uvicorn`
- Auto-reload enabled (hot reload on file changes)
- Automatically sets `USE_FAKE_DATA=true`
- Output goes to integrated terminal
- Press **F5** to start the app

#### `settings.json` - Editor Settings
- Black formatter for auto-formatting
- Ruff for linting
- Pytest configuration for testing
- Auto-format on save

#### `extensions.json` - Recommended Extensions
- Python (official VS Code extension)
- Pylance (static analysis)
- Ruff (fast Python linter)
- Black Formatter
- REST Client (API testing)
- Thunder Client (API testing alternative)
- others for development

#### `.vscode/README.md` - Documentation
Complete guide for using VS Code with this project

---

## 2. Fake Data Support

### Environment Variable: `USE_FAKE_DATA`

Add to `.env` file:
```bash
USE_FAKE_DATA=true
```

When set to `true`, the application loads sample data on startup:
- **2 demo users** with login credentials
- **3 sample devices** (gaming PC, Raspberry Pi, custom arcade)
- **10+ sample ROMs** across multiple systems
- **8 game play logs** with timestamps

#### Demo Credentials

**User 1:**
- Email: `demo@example.com`
- Password: `DemoPass123`

**User 2:**
- Email: `arcade@example.com`  
- Password: `ArcadePass123`

### Code Changes

#### `src/overmind/db.py`
Added new method:
```python
def populate_fake_data(self):
    """Populate database with sample data for testing."""
```

Creates:
- Sample users with hashed passwords
- 3 devices with realistic Batocera system info
- ROMs for SNES, Genesis, NES systems
- Game play history for each device

#### `src/overmind/main.py`
Updated startup event:
```python
@app.on_event("startup")
async def startup_event():
    # ... existing code ...
    if os.getenv("USE_FAKE_DATA", "").lower() == "true":
        db.populate_fake_data()
        # Print sample data info
```

#### `.env.example`
Added new configuration:
```bash
USE_FAKE_DATA=true
```

---

## Quick Start (with F5)

### Step 1: Install Dependencies
```bash
cd /Users/Jerrod/Documents/github/batocera.overmind
make install
```

### Step 2: Open in VS Code
```bash
code .
```

### Step 3: Press F5 to Run
- VS Code will start the development server
- Sample data automatically loads
- Visit http://localhost:8000
- API docs at http://localhost:8000/docs

### Step 4: Login with Demo Account
- Email: `demo@example.com`
- Password: `DemoPass123`
- You'll see 2 pre-configured devices with ROMs and game history

---

## Features

### F5 Benefits
✅ **One-key start** - Just press F5
✅ **Hot reload** - Changes auto-reload without restart
✅ **Integrated terminal** - See logs in VS Code
✅ **Debugging** - Set breakpoints, inspect variables
✅ **Sample data** - Auto-loaded for testing

### Fake Data Benefits
✅ **Quick testing** - No need to create test data manually
✅ **Demo accounts** - Show clients/users what it looks like
✅ **Realistic data** - Sample devices with real Batocera info
✅ **Multiple systems** - SNES, Genesis, NES examples
✅ **Game logs** - Sample play history for testing

---

## File Structure

```
.vscode/
├── launch.json          # F5 run configuration
├── settings.json        # Editor settings
├── extensions.json      # Recommended extensions
└── README.md           # VS Code setup guide
```

---

## Environment Variable Details

### `USE_FAKE_DATA`

**Values:**
- `true` - Load sample data on startup
- `false` or not set - Use empty database
- Case-insensitive

**What happens on startup:**

With `USE_FAKE_DATA=true`:
```
🎮 Batocera Overmind API started
📖 API Documentation: http://localhost:8000/docs
🏠 UI: http://localhost:8000/

📚 Loading sample data...
✓ Sample data loaded successfully!
  • 2 demo users
  • 3 sample devices
  • 10+ sample ROMs
  • 8 sample game plays

  Demo Credentials:
  Email: demo@example.com
  Password: DemoPass123

  Or:
  Email: arcade@example.com
  Password: ArcadePass123
```

---

## Sample Data Details

### Devices Included

**Device 1: "Living Room Cabinet"**
- Type: AMD Ryzen 7 gaming PC
- CPU: AMD Ryzen 7 7800X3D (8 cores, 16 threads)
- RAM: 32 GB
- ROMs: SNES (3 games), Genesis (2 games)
- Game logs: 4 sessions

**Device 2: "Bedroom Pi"**
- Type: Raspberry Pi 4
- CPU: ARM Cortex-A72 (4 cores)
- RAM: 4 GB
- ROMs: NES (2 games)
- Game logs: 2 sessions

**Device 3: "Game Room Arcade"**
- Type: Custom Intel PC
- CPU: Intel Core i7-12700K (12 cores, 20 threads)
- RAM: 32 GB
- Display: 3440x1440 (ultrawide)
- ROMs: SNES (2 games)
- Game logs: 2 sessions

### Sample ROMs

**SNES:**
- Super Mario Bros
- The Legend of Zelda
- Super Metroid
- Final Fantasy VI
- Chrono Trigger

**Genesis:**
- Sonic the Hedgehog
- Sonic the Hedgehog 2

**NES:**
- Super Mario Bros
- Donkey Kong

---

## Troubleshooting

**Issue: F5 doesn't start the app**
- Solution: Make sure you have Python installed and in PATH
- Check: Run `python --version` in terminal

**Issue: Sample data not loading**
- Check: `USE_FAKE_DATA=true` is set in launch.json or .env
- Check: Terminal output shows "Loading sample data..."

**Issue: Port 8000 already in use**
- Solution: Kill the other process or change port in launch.json

**Issue: Module not found errors**
- Solution: Run `make install` to install dependencies first

---

## Next Steps

1. **Press F5** to run the app
2. **Visit** http://localhost:8000
3. **Login** with demo@example.com / DemoPass123
4. **Explore** the dashboard, ROMs, and game history
5. **Test** the API at http://localhost:8000/docs

---

## Tips & Tricks

### Hot Reload
- Edit Python files while F5 is running
- Server automatically reloads (usually < 1 second)
- No need to restart!

### Debugging
- Click on line numbers to set breakpoints
- Step through code with F10/F11
- Inspect variables in debug panel
- Use Debug Console to evaluate expressions

### Testing APIs
- Install "REST Client" VS Code extension
- Create `.http` or `.rest` files
- Send requests directly from editor

### View Logs
- Terminal output shows server logs
- Check "Output" panel for debugging info
- Use `print()` statements for debugging

---

## API Endpoints (with sample data)

All endpoints work with sample data:

```bash
# Login
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@example.com","password":"DemoPass123"}'

# Get devices (after login with token)
curl -X GET http://localhost:8000/api/devices \
  -H "Authorization: Bearer YOUR_TOKEN"

# Get ROMs
curl -X GET http://localhost:8000/api/devices/arcade-cabinet-001/roms \
  -H "Authorization: Bearer YOUR_TOKEN"

# Get game history
curl -X GET http://localhost:8000/api/devices/arcade-cabinet-001/gamelogs \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

**Created for**: VS Code integration and development workflow
**Last updated**: May 7, 2026
