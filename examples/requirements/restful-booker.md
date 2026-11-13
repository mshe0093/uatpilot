# Hotel Booking API: Release 2.4 Requirements

**System:** Booking API (sandbox: https://restful-booker.herokuapp.com)
**Owner:** Reservations team
**Audience:** UAT testers, release manager

The Booking API lets the front desk and partner sites create, look up, change and cancel guest
bookings. This release must be signed off in UAT before go-live.

Test credentials for the UAT environment: username `admin`, password `password123`
(`POST /auth` with a JSON body `{"username": ..., "password": ...}` returns `{"token": "..."}`;
send it on protected calls as the header `Cookie: token=<token>`).

A booking looks like:

```json
{
  "firstname": "Jim",
  "lastname": "Brown",
  "totalprice": 111,
  "depositpaid": true,
  "bookingdates": { "checkin": "2026-01-01", "checkout": "2026-01-05" },
  "additionalneeds": "Breakfast"
}
```

Requests and responses are JSON (`Content-Type: application/json`, `Accept: application/json`).

---

## US-1 Health check
As an operator, I want a health endpoint so monitoring can tell if the API is up.

- **AC-1.1** `GET /ping` responds with a success status (2xx) when the service is up.

## US-2 Create a booking
As a front-desk agent, I want to create a booking for a guest.

- **AC-2.1** `POST /booking` with a complete, valid booking returns 200 and a body containing a
  numeric `bookingid` and a `booking` object that echoes every field that was sent.
- **AC-2.2** The new booking can immediately be fetched with `GET /booking/{bookingid}` and
  returns the same guest name, price and dates.
- **AC-2.3** A booking missing required fields (for example only `firstname`) is rejected with a
  `400 Bad Request`, not accepted and not a server error.
- **AC-2.4** A booking with a negative `totalprice` is rejected (4xx).
- **AC-2.5** A booking whose `checkout` date is before its `checkin` date is rejected (4xx).

## US-3 Look up bookings
As a front-desk agent, I want to find bookings.

- **AC-3.1** `GET /booking` returns 200 and a JSON array of objects that each contain a
  `bookingid`.
- **AC-3.2** `GET /booking?firstname=<name>&lastname=<name>` returns only bookings for that guest:
  after creating a booking with a unique name, filtering by that name returns its `bookingid`.
- **AC-3.3** `GET /booking/{id}` for an id that does not exist returns `404`.

## US-4 Authentication
As the security team, we want changes to bookings to require a login.

- **AC-4.1** `POST /auth` with the valid UAT credentials returns 200 and a non-empty `token`.
- **AC-4.2** `POST /auth` with a wrong password must not return a token, and must respond with
  `401 Unauthorized`.
- **AC-4.3** Updating (`PUT`) or cancelling (`DELETE`) a booking without a token returns `403`.

## US-5 Change a booking
As a front-desk agent, I want to correct a booking.

- **AC-5.1** `PUT /booking/{id}` with a token and a complete booking replaces the booking; the
  response (200) and a subsequent `GET` show the new values.
- **AC-5.2** `PATCH /booking/{id}` with a token and only `{"firstname": ...}` changes the first
  name and leaves every other field unchanged.

## US-6 Cancel a booking
As a front-desk agent, I want to cancel a booking.

- **AC-6.1** `DELETE /booking/{id}` with a token cancels the booking and responds with a success
  status. The API standard for a successful delete with no body is `204 No Content`.
- **AC-6.2** After cancellation, `GET /booking/{id}` returns `404`.

## Non-functional
- **NFR-1** Single-booking reads (`GET /booking/{id}`) respond in under 3000 ms in UAT.
