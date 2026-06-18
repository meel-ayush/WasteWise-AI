import os
import html
import resend

resend.api_key = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "WasteWise AI <onboarding@resend.dev>")


def _send(to: str, subject: str, html: str) -> bool:
    """Internal send helper. Returns True on success."""
    if not resend.api_key:
        print(f"[Email] No RESEND_API_KEY set. Would send to {to }: {subject }")
        return False
    try:
        resend.Emails.send(
            {
                "from": FROM_EMAIL,
                "to": [to],
                "subject": subject,
                "html": html,
            }
        )
        return True
    except Exception as e:
        print(f"[Email] Send failed to {to }: {e }")
        return False


def send_welcome_email(email: str, name: str) -> bool:
    """Welcome email for new marketplace customer registrations."""
    safe_name = html.escape(name or "")
    frontend_url = html.escape(os.getenv("FRONTEND_URL", "https://wastewise.vercel.app"))
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;padding:32px">
      <h1 style="color:#10b981;font-size:1.5rem;margin-bottom:8px">ðŸŒ¿ Welcome to WasteWise!</h1>
      <p style="color:#374151">Hi {safe_name},</p>
      <p style="color:#374151">Your marketplace account is ready. You can now browse discounted
      closing stock from hawker stalls near you and help reduce food waste.</p>
      <a href="{frontend_url}/marketplace"
         style="display:inline-block;background:#10b981;color:#fff;padding:12px 24px;
                border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px">
        Browse the Marketplace &rarr;
      </a>
      <p style="color:#9ca3af;font-size:0.8rem;margin-top:32px">
        WasteWise AI &mdash; Reducing food waste one portion at a time ðŸŒ±
      </p>
    </div>
    """
    return _send(email, "Welcome to WasteWise! ðŸŒ¿", html)



def send_order_confirmation(
    email: str,
    name: str,
    order_id: str,
    restaurant_name: str,
    items: list,
    total_rm: float,
    pickup_time: str,
) -> bool:
    """Order confirmation email for marketplace customers."""
    safe_name = html.escape(name or "")
    safe_order_id = html.escape(order_id or "")
    safe_restaurant = html.escape(restaurant_name or "")
    safe_pickup = html.escape(pickup_time or "")
    items_html = "".join(
        f"<tr><td style='padding:4px 8px'>{html.escape(str(i.get('item','')))}</td>"
        f"<td style='padding:4px 8px;text-align:right'>x{int(i.get('qty', 1))}</td>"
        f"<td style='padding:4px 8px;text-align:right'>RM {float(i.get('line_total_rm', 0)):.2f}</td></tr>"
        for i in items
    )
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;padding:32px">
      <h1 style="color:#10b981;font-size:1.3rem">&#10003; Order Confirmed!</h1>
      <p style="color:#374151">Hi {safe_name}, your order from <strong>{safe_restaurant}</strong> is confirmed.</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <thead><tr style="background:#f3f4f6">
          <th style="padding:8px;text-align:left">Item</th>
          <th style="padding:8px;text-align:right">Qty</th>
          <th style="padding:8px;text-align:right">Price</th>
        </tr></thead>
        <tbody>{items_html}</tbody>
        <tfoot><tr style="border-top:2px solid #e5e7eb">
          <td colspan="2" style="padding:8px;font-weight:700">Total</td>
          <td style="padding:8px;text-align:right;font-weight:700">RM {total_rm:.2f}</td>
        </tr></tfoot>
      </table>
      <p style="background:#fef3c7;padding:12px;border-radius:8px;color:#92400e">
        &#128336; <strong>Pick up before {safe_pickup}</strong> &mdash; Pay at the counter when you collect.
      </p>
      <p style="color:#9ca3af;font-size:0.75rem">Order ID: {safe_order_id}</p>
    </div>
    """
    return _send(email, f"Order Confirmed \u2014 {restaurant_name} \U0001f37d\ufe0f", html)



def send_reservation_reminder(
    email: str, name: str, item_name: str, restaurant_name: str, pickup_deadline: str
) -> bool:
    """1-hour reminder for marketplace item reservations."""
    safe_name = html.escape(name or "")
    safe_item = html.escape(item_name or "")
    safe_restaurant = html.escape(restaurant_name or "")
    safe_deadline = html.escape(pickup_deadline or "")
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;padding:32px">
      <h1 style="color:#f59e0b;font-size:1.3rem">&#9200; Reservation Reminder</h1>
      <p style="color:#374151">Hi {safe_name},</p>
      <p style="color:#374151">Your reserved <strong>{safe_item}</strong> at
      <strong>{safe_restaurant}</strong> is waiting for you!</p>
      <p style="background:#fef3c7;padding:12px;border-radius:8px;color:#92400e">
        &#9889; Pick up before <strong>{safe_deadline}</strong> or your reservation will be released.
      </p>
      <p style="color:#6b7280;font-size:0.85rem">
        Pay at the counter when you collect. No payment needed now.
      </p>
    </div>
    """
    return _send(email, f"\u23f0 Collect your {item_name} before {pickup_deadline}!", html)



def send_account_deletion_confirmation(email: str, name: str) -> bool:
    """Confirmation email when a customer deletes their account."""
    safe_name = html.escape(name or "")
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;padding:32px">
      <h1 style="color:#374151;font-size:1.3rem">Account Deleted</h1>
      <p style="color:#374151">Hi {safe_name},</p>
      <p style="color:#374151">Your WasteWise marketplace account and all associated data
      have been permanently deleted as requested.</p>
      <p style="color:#6b7280;font-size:0.85rem">
        If you change your mind, you can always create a new account at wastewise.vercel.app.
      </p>
    </div>
    """
    return _send(email, "Your WasteWise account has been deleted", html)


