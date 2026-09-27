# Sign-in email templates

For Supabase **Authentication → Emails → Templates** (editable once custom SMTP is set up; see "Setting up the sign-in emails" below). Keep `{{ .ConfirmationURL }}` and `{{ .Token }}` exactly as written: Supabase fills in the link and the code (six to ten digits, as set under Authentication → Providers → Email → Email OTP Length; this project uses eight).

The code matters on an iPhone: an app added to the Home Screen never receives the email's link (it opens in the browser), so there the person types the code instead.

## Confirm signup

Subject: `Confirm your email for Ignatius at Home`

```html
<h2>Welcome to Ignatius at Home</h2>
<p>You (or someone using your address) asked to sign in to Ignatius at Home, the app that turns your prayer material into a guided audio retreat.</p>
<p><a href="{{ .ConfirmationURL }}">Confirm my email and open my retreats</a></p>
<p>On your iPhone's Home Screen app, type this code instead: <b style="font-size:22px;letter-spacing:4px">{{ .Token }}</b></p>
<p>Open the link on the device you want to pray on. If you didn't ask for this, you can ignore this email.</p>
```

## Magic Link

Subject: `Your sign-in link for Ignatius at Home`

```html
<h2>Sign in to Ignatius at Home</h2>
<p><a href="{{ .ConfirmationURL }}">Open my retreats</a></p>
<p>Using the app from your iPhone's Home Screen? Type this code there instead of tapping the link:</p>
<p style="font-size:28px;letter-spacing:6px;font-weight:bold">{{ .Token }}</p>
<p>The link and the code work once and expire in an hour. If you didn't ask to sign in, you can ignore this email.</p>
```

## Change Email Address

Subject: `Keep your Ignatius at Home retreats`

```html
<h2>Keep your retreats</h2>
<p>Confirm this address to turn your guest session into an account. Then sign in with it on your phone to see the same retreats.</p>
<p><a href="{{ .ConfirmationURL }}">Confirm my email</a></p>
```

## Setting up the sign-in emails

Supabase's built-in sender is for testing: it sends as "Supabase Auth", only a few emails an hour, and its templates can't be edited. With your own SMTP server the emails come from Ignatius at Home, and the templates above can be used.

**With Google Workspace** (collier.phd's mail is already on Google, so no DNS changes are needed):
1. On the Google account that will send (it needs 2-Step Verification), create an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), named "Supabase". Copy the 16 characters.
2. In Supabase: **Authentication → Emails → SMTP Settings**, turn on custom SMTP: sender email (the Google address, or an alias set up as "Send mail as" in Gmail), sender name `Ignatius at Home`, host `smtp.gmail.com`, port `465`, username the Google address, password the app password.
3. **Authentication → Rate Limits**: raise emails per hour to about 30.
4. **Authentication → Emails → Templates**: paste the three templates above (subject and body).
5. **Authentication → URL Configuration**: the Site URL is the live app, `https://bcollier.github.io/ignatius-hw4-web/`, and it's in the redirect URLs.
6. Test: send yourself a sign-in link from the live app, and check the sender, the design, the link and the code.

**With Resend instead** (a separate sending service; free for a few thousand emails a month): add `collier.phd` under Domains, add the DNS records it shows (DKIM, SPF and MX on a `send` subdomain) where the domain's DNS is managed, wait for Verified, and create an API key with sending access. Then use host `smtp.resend.com`, port `465`, username `resend`, and the API key as the password.
