import { createRouter, createWebHistory } from 'vue-router'
import AdminUsersView from './views/AdminUsersView.vue'
import ApplicationDetailView from './views/ApplicationDetailView.vue'
import ApplicationsView from './views/ApplicationsView.vue'
import CandidateReviewView from './views/CandidateReviewView.vue'
import DocumentEvidenceView from './views/DocumentEvidenceView.vue'
import LoginView from './views/LoginView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/login' },
    { path: '/login', component: LoginView },
    { path: '/applications', component: ApplicationsView },
    { path: '/applications/:id', component: ApplicationDetailView },
    { path: '/applications/:id/candidates', component: CandidateReviewView },
    { path: '/documents/:id/evidence', component: DocumentEvidenceView },
    { path: '/admin/users', component: AdminUsersView },
  ],
})
