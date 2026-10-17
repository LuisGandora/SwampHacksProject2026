// Auth0 sign-in. Set VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID and VITE_AUTH0_AUDIENCE in frontend/.env.
// Without them the app runs signed-out against a local API started with PLANIT_DEV=1 (a "dev" user).
import { useAuth0 } from "@auth0/auth0-react";

export const AUTH0 = {
  domain: import.meta.env.VITE_AUTH0_DOMAIN,
  clientId: import.meta.env.VITE_AUTH0_CLIENT_ID,
  audience: import.meta.env.VITE_AUTH0_AUDIENCE,
};
export const authEnabled = Boolean(AUTH0.domain && AUTH0.clientId && AUTH0.audience);

const LOCAL = { enabled: false, isLoading: false, isAuthenticated: true, user: { name: "Local dev" },
                login: () => {}, logout: () => {}, getToken: null };

// authEnabled never changes while the app runs, so calling the hook conditionally is safe here.
export function useAuth() {
  if (!authEnabled) return LOCAL;
  const a = useAuth0(); // eslint-disable-line react-hooks/rules-of-hooks
  return {
    enabled: true, isLoading: a.isLoading, isAuthenticated: a.isAuthenticated, user: a.user,
    login: () => a.loginWithRedirect(),
    logout: () => a.logout({ logoutParams: { returnTo: window.location.origin } }),
    getToken: a.getAccessTokenSilently,
  };
}
