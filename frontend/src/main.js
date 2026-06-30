import Vue from 'vue'
import App from './StudyLoopApp.vue'
import './assets/studyloop.css'

Vue.config.productionTip = false

new Vue({
  render: (h) => h(App),
}).$mount('#app')
