const { defineConfig } = require('@vue/cli-service')

module.exports = defineConfig({
  transpileDependencies: true,
  outputDir: 'dist',
  devServer: {
    proxy: {
      '/health': {
        target: process.env.VUE_APP_API_PROXY_TARGET || 'http://localhost:8765',
        changeOrigin: true,
      },
      '/materials': {
        target: process.env.VUE_APP_API_PROXY_TARGET || 'http://localhost:8765',
        changeOrigin: true,
      },
      '/study': {
        target: process.env.VUE_APP_API_PROXY_TARGET || 'http://localhost:8765',
        changeOrigin: true,
      },
      '/notes': {
        target: process.env.VUE_APP_API_PROXY_TARGET || 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },
})
