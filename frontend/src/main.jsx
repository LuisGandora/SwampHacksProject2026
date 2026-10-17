import React from "react";
import { createRoot } from "react-dom/client";
import { Auth0Provider } from "@auth0/auth0-react";
import App from "./App.jsx";
import { AUTH0, authEnabled } from "./auth.js";
import "./App.css";

// Refresh tokens (rotation on in Auth0) keep phones signed in: Safari blocks the hidden-iframe renewal.
const app = authEnabled ? (
  <Auth0Provider domain={AUTH0.domain} clientId={AUTH0.clientId} useRefreshTokens cacheLocation="localstorage"
                 authorizationParams={{ redirect_uri: window.location.origin, audience: AUTH0.audience,
                                        scope: "openid profile email offline_access" }}>
    <App />
  </Auth0Provider>
) : <App />;

createRoot(document.getElementById("root")).render(app);
