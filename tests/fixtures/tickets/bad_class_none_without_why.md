Title: Cart shows the shipping cost before payment

## For the human
- **What:** стоимость доставки показывается в корзине, а не только после ввода карты.
- **Goal:** покупатель видит итоговую цену заказа до оплаты, а не в её конце.
- **Plan:** корзина спрашивает тот же расчёт доставки, что и страница оплаты,
  как только введён почтовый индекс.
- **Result:** цена доставки в корзине равна цене на оплате на четырёх индексах: трёх названных и одном вне названных (50667, другая зона); показывает тест расчёта доставки.
- **Undo:** two-way door (один откат); touches каждый заказ, сделанный через корзину.

## For the agent
Facts: the payment page prices shipping with `shipping_quote(postcode)` (checkout/payment.py:88); the cart page has no call to it (checkout/cart.py:31).
Rule: the cart calls the same `shipping_quote(postcode)` as the payment page; no second price table.
Weight: 3

### Acceptance

| row | check | pass |
|---|---|---|
| 1 | cart shipping cost for 10115 | equals the payment page cost for 10115 |
| 2 | cart shipping cost for 20095 | equals the payment page cost for 20095 |
| 3 | cart shipping cost for 50667, a zone none of the named ones is in | equals the payment page cost for 50667 |
| 4 after merge | abandoned carts at the payment step | under 30 % in the weekly report |

Class: none
Same object: the payment page's `shipping_quote(postcode)` reads the same postcode; with both, cart and payment show one price from one function.
Not in this ticket: the shipping rates themselves; the payment page's layout.
