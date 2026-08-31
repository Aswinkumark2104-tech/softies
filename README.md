# softies

Complete local demo of an Indian online toy store with customer pages, cart, wishlist, COD checkout, order creation, admin dashboard, status updates and CSV export.

## Quick local demo
Open `index.html` in a browser, or serve the folder with a local static server.

Demo admin login:
- Username: `admin`
- Password: `ToyLand@2026`

## Demo storage
- `toylandCart`
- `toylandWishlist`
- `toylandOrders`

Demo mode uses browser `localStorage` and is **not secure for production**.

## Production
1. `cp .env.example .env`
2. Fill database, JWT, email and payment secrets.
3. `npm install`
4. Implement database models/controllers and protected API routes.
5. `npm run dev`

For production, calculate prices and totals on the server, hash admin passwords, protect APIs, validate all input, add rate limiting, HTTPS, transactional email and a verified payment gateway.

## Deployment
- Frontend: deploy static pages to suitable web hosting.
- Backend: deploy Node.js service.
- Database: connect MongoDB or Supabase.
- Add environment variables in the hosting dashboard.
- Connect a custom domain and enable HTTPS.
