/**
 * Light ("nordly") and dark ("nordly-night") DaisyUI themes. Applied on layout
 * mount so the whole page follows the saved choice, not only the open chat panel.
 */

const LIGHT = "nordly";
const DARK = "nordly-night";
const STORAGE_KEY = "theme";

const isDark = ref(false);

function apply(dark: boolean) {
  isDark.value = dark;
  document.documentElement.setAttribute("data-theme", dark ? DARK : LIGHT);
}

export const useTheme = () => {
  const init = () => {
    const saved = localStorage.getItem(STORAGE_KEY);
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    apply(saved === "dark" || (!saved && prefersDark));
  };

  const toggle = () => {
    apply(!isDark.value);
    localStorage.setItem(STORAGE_KEY, isDark.value ? "dark" : "light");
  };

  return { isDark: readonly(isDark), init, toggle };
};
