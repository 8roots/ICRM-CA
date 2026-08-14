import { createRouter, createWebHistory } from 'vue-router'
import AdminRulesView from './views/AdminRulesView.vue'
import AdminTemplatesView from './views/AdminTemplatesView.vue'
import AdminUsersView from './views/AdminUsersView.vue'
import ApplicationDetailView from './views/ApplicationDetailView.vue'
import ApplicationsView from './views/ApplicationsView.vue'
import CandidateReviewView from './views/CandidateReviewView.vue'
import CompletenessView from './views/CompletenessView.vue'
import DocumentEvidenceView from './views/DocumentEvidenceView.vue'
import LoginView from './views/LoginView.vue'
import RedlineView from './views/RedlineView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/login' },
    { path: '/login', component: LoginView },
    { path: '/applications', component: ApplicationsView },
    { path: '/applications/:id', component: ApplicationDetailView },
    { path: '/applications/:id/candidates', component: CandidateReviewView },
    { path: '/applications/:id/completeness', component: CompletenessView },
    { path: '/applications/:id/redline', component: RedlineView },
    { path: '/documents/:id/evidence', component: DocumentEvidenceView },
    { path: '/admin/users', component: AdminUsersView },
    { path: '/admin/templates', component: AdminTemplatesView },
    { path: '/admin/rules', component: AdminRulesView },
  ],
})
