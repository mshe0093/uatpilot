# Swag Labs Web Shop: Storefront UAT Requirements

**System:** Swag Labs storefront (sandbox: https://www.saucedemo.com)
**Owner:** E-commerce team
**Audience:** UAT testers, release manager

Customers log in, browse products, add them to a cart and check out.

UAT test accounts (all use the password `secret_sauce`):
- `standard_user`: a normal customer account
- `locked_out_user`: an account that has been locked by the fraud team

---

## US-1 Login
- **AC-1.1** A customer who enters a valid username and password and presses **Login** is taken
  to the products page (URL contains `/inventory.html`) and sees the heading "Products".
- **AC-1.2** A locked-out account cannot log in: the customer stays on the login page and sees an
  error message that says the user has been locked out.
- **AC-1.3** Wrong credentials show an error message saying the username and password do not
  match, and the customer stays on the login page.
- **AC-1.4** Pressing **Login** with an empty username shows an error saying the username is
  required.

## US-2 Product catalogue
- **AC-2.1** After logging in, the products page lists exactly 6 products.
- **AC-2.2** Choosing the sort option "Price (low to high)" puts the cheapest product, priced
  $7.99, first in the list.
- **AC-2.3** Choosing the sort option "Name (Z to A)" puts "Test.allTheThings() T-Shirt (Red)"
  first in the list.

## US-3 Cart
- **AC-3.1** Adding the "Sauce Labs Backpack" to the cart shows a cart badge with the number 1.
- **AC-3.2** Adding two different products shows a cart badge with the number 2, and the cart
  page lists both products.
- **AC-3.3** Removing a product from the cart page removes it from the list and clears the badge
  when the cart becomes empty.

## US-4 Checkout
- **AC-4.1** From the cart, a customer can press **Checkout**, enter first name, last name and
  postal code, continue, see the overview page, press **Finish**, and see the confirmation
  "Thank you for your order!".
- **AC-4.2** Continuing the checkout information step with an empty first name shows an error
  saying the first name is required.

## US-5 Logout
- **AC-5.1** Opening the menu and choosing **Logout** returns the customer to the login page.
