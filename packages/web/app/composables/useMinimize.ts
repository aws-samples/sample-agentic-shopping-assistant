/**
 * 최소화 상태를 관리하는 composable
 * 전역 상태로 관리되어 모든 컴포넌트에서 동일한 상태를 공유합니다.
 */

// 전역 상태로 관리
const globalMinimizeState = ref(false);

export const useMinimize = () => {
  const minimize = () => {
    globalMinimizeState.value = true;
  };

  const maximize = () => {
    globalMinimizeState.value = false;
  };

  const toggleMinimize = () => {
    globalMinimizeState.value = !globalMinimizeState.value;
  };

  return {
    isMinimized: readonly(globalMinimizeState),
    minimize,
    maximize,
    toggleMinimize,
  };
};
