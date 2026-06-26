---
name: reservation
description: Use when a customer wants to make, view, change, or cancel a reservation, or asks about available dates/times/slots.
---

# Reservation handling

Use this skill when you get a reservation-related request from the customer.

- Check live reservation status and open slots from the admin dashboard: https://saptarshimazumder.github.io/sakana-sushi/admin.html
- If you don't have the admin login credentials, use the `simple_login` tool or the `browser` tool to log in. If you still can't get in, tell the customer you can't reach the booking system right now and offer to follow up — do NOT guess.
- NEVER ask the customer to login to the admin url or admin page. Remember, the person you are chatting with is a CUSTOMER, NOT a system admin
- You are ALLOWED to VIEW, ADD, EDIT, and CANCEL reservations on the customer's behalf.
- ALWAYS work from the LIVE dashboard — never quote availability or booking details from memory.
- For any change to a booking (new / edit / cancel), read the details back to the customer and get their explicit confirmation BEFORE saving, then notify them of the result on the same channel they messaged on.

## Required details for a booking

Name, date, time, party size, course (if any), and at least one contact — phone OR email (one is mandatory). Ask for anything missing before creating the booking.

** NEW RESERVATION **

- Click the "NEW RESERVATION" button to open the form.
- SET the Date, Seating (this is the time of the reservation), Table, Party size, Guest name, and Phone and/or email (at least one is mandatory).
- Set the status to "confirmed".
- If the customer has any special requests (allergies, occasion, seating preference, etc), add them to Notes.
- Save, then notify the customer on the same channel with the confirmed date, time, party size, and table.

** EDIT RESERVATION **

- Find the existing reservation on the dashboard (search by guest name, date, phone, or email).
- Open it and change ONLY the fields the customer asked to change (e.g. time, party size, course, notes).
- If the change needs a different slot or table, confirm that slot is actually free on the live dashboard before saving.
- Read the updated details back to the customer, save, then confirm the change on the same channel.

** CANCEL RESERVATION **

- Find the reservation on the dashboard and confirm with the customer that it's the right one (name + date + time) BEFORE cancelling — a cancellation is irreversible.
- Cancel it / set its status to "cancelled".
- Confirm to the customer that the booking has been cancelled, on the same channel.

** VIEW / CHECK AVAILABILITY **

- To answer "do you have a table on X?" or "what's my booking?", read the LIVE dashboard first.
- For availability: report the open seatings/tables for the date and party size asked about — don't promise a slot you haven't seen free on the dashboard.
- For an existing booking: look it up by name/date/contact and read the details back. Never reveal one customer's booking to a different person.

** ACCEPT / REJECT A REQUEST **

- Check the live dashboard to see whether the requested slot is available.
- If available: create/confirm the booking (NEW RESERVATION above) and tell the customer it's confirmed.
- If not available: politely reject, explain it's full for that slot, and offer the nearest alternative dates/times you can actually see open on the dashboard.
