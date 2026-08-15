import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { SetupGuard } from "./components/SetupGuard";
import { Layout } from "./components/Layout";
import { Toaster } from "./components/Toaster";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { AuthCallbackPage } from "./pages/AuthCallbackPage";
import { DatasetDetailPage } from "./pages/DatasetDetailPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { ModelShowcasePage } from "./pages/ModelShowcasePage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { SetupPage } from "./pages/SetupPage";
import { WorkviewPage } from "./pages/WorkviewPage";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Toaster />
      <Routes>
        <Route element={<SetupGuard />}>
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/forgot" element={<ForgotPasswordPage />} />
          <Route path="/reset" element={<ResetPasswordPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />

          {/* Workview is full-screen, no shell */}
          <Route element={<ProtectedRoute />}>
            <Route
              path="/projects/:projectId/datasets/:datasetId/workview/:imageId"
              element={<WorkviewPage />}
            />
          </Route>

          {/* App shell */}
          <Route element={<ProtectedRoute />}>
            <Route element={<Layout />}>
              <Route path="/" element={<Navigate to="/projects" replace />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/model" element={<ModelShowcasePage />} />
              <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
              <Route
                path="/projects/:projectId/datasets/:datasetId"
                element={<DatasetDetailPage />}
              />
              <Route path="/admin/users" element={<AdminUsersPage />} />
              <Route path="*" element={<div className="p-8 text-neutral-500">Not found</div>} />
            </Route>
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
