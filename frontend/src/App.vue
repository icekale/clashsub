<script setup>
// Naive UI's color utility cannot parse OKLCH. These values are the sRGB
// boundary conversions of the OKLCH design tokens defined in styles.css.
const themeOverrides = {
  common: {
    primaryColor: '#096165',
    primaryColorHover: '#137479',
    primaryColorPressed: '#005053',
    primaryColorSuppl: '#096165',
    infoColor: '#096165',
    successColor: '#1f6b41',
    warningColor: '#b27a00',
    errorColor: '#b02a2d',
    borderRadius: '8px',
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
}
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-dialog-provider>
      <n-message-provider>
        <router-view />
      </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>
