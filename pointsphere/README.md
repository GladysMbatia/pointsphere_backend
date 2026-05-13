# PointSphere – Local Backend

Django + SQLite backend for the PointSphere auth system.

## Requirements
```
pip install django django-cors-headers
```

## Start the server
```bash
cd pointsphere
bash start_server.sh
# or: python manage.py runserver 8000
```

## API Endpoints

### POST /api/register/
```json
{ "name": "Jane Doe", "phone": "712345678", "pin": "1234", "role": "customer" }
```
Roles: `customer` | `partner` | `admin`

### POST /api/login/
```json
{ "phone": "712345678", "pin": "1234", "role": "customer" }
```
Returns: `{ "token": "...", "role": "customer", "name": "Jane Doe" }`

## Database
SQLite file → `db.sqlite3` (auto-created on first run, no setup needed)

## Using the HTML files
Open `index.html` directly in your browser — CORS is fully open so all
fetch calls to `http://127.0.0.1:8000/api` will work.
