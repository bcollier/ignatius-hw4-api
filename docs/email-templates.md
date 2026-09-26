# Sign-in email templates

For Supabase **Authentication → Emails → Templates** (editable once custom SMTP is set up; see the To do in [IMPROVEMENTS.md](IMPROVEMENTS.md)). Keep `{{ .ConfirmationURL }}` exactly as written.

## Confirm signup

Subject: `Confirm your email for Ignatius at Home`

```html
<h2>Welcome to Ignatius at Home</h2>
<p>You (or someone using your address) asked to sign in to Ignatius at Home, the app that turns your prayer material into a guided audio retreat.</p>
<p><a href="{{ .ConfirmationURL }}">Confirm my email and open my retreats</a></p>
<p>Open the link on the device you want to pray on. If you didn't ask for this, you can ignore this email.</p>
```

## Magic Link

Subject: `Your sign-in link for Ignatius at Home`

```html
<h2>Sign in to Ignatius at Home</h2>
<p><a href="{{ .ConfirmationURL }}">Open my retreats</a></p>
<p>The link works once and expires in an hour. If you didn't ask to sign in, you can ignore this email.</p>
```

## Change Email Address

Subject: `Keep your Ignatius at Home retreats`

```html
<h2>Keep your retreats</h2>
<p>Confirm this address to turn your guest session into an account. Then sign in with it on your phone to see the same retreats.</p>
<p><a href="{{ .ConfirmationURL }}">Confirm my email</a></p>
```
