# Frontend

React + Vite + TypeScript control center shell for Dormero Viktoria.

## Run

```bash
npm install
npm run dev
```

## Booking Calendar Setup

Create:

- `/Users/peter/clar/frontend/.env`

Add:

- `VITE_SUPPORT_BOOKING_URL` with your public Google booking calendar link (appointment schedule URL).
- This is the only required booking variable used by the Smart Insights `Book Support` button.

You can copy the template from:

- `/Users/peter/clar/frontend/.env.example`

Note:

- Google appointment booking pages do not currently expose a documented URL query prefill for custom form fields.
- If you want agents to include report context, add a custom booking question in your appointment schedule (for example: "Paste Smart Insights summary") and have users paste it when booking.

## Logo Asset

Place the Dormero brand logo at:

- `/Users/peter/clar/frontend/public/domero_logo_nbg.png`

A placeholder logo file is included and can be replaced with your official asset using the same filename.
