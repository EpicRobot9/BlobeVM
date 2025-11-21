BlobeVM Manager — Dashboard v2

Quick start (development):

1. Install dependencies from `dashboard_v2`:

```bash
cd dashboard_v2
npm install
npm run dev
```

2. The dev server runs with base `http://localhost:5173/Dashboard/` (Vite). The Flask backend will serve the production build from `dashboard_v2/dist` when built.

Production build:

```bash
cd dashboard_v2
npm run build
# copy or leave the `dist` folder next to the server package; the Flask app will serve it at /Dashboard/
```

Integration notes:
- The admin password for the v2 dashboard is read from the old dashboard settings file key `new_dashboard_admin_password` (managed only by the old dashboard UI). Set it there to enable v2 login.
- The backend exposes `/Dashboard/api/auth/login` for obtaining a short-lived token and `/Dashboard/api/auth/status` for status checks.
- The frontend uses Authorization: Bearer <token> for API calls to `/Dashboard/api/*`.
