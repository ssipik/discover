variable "image_repo" {
  type    = string
  default = "getregistry.azurecr.io/rag_discover"
}
variable "image_tag" {
  type = string
}

# Stage 1 (DISCOVER). Registered by bitbucket-pipelines.yml, started by Airflow:
#   nomad job dispatch -meta run_id=<airflow run_id> rag-discover-template
job "rag-discover-template" {
  datacenters = ["dc1"]
  type        = "batch"

  parameterized {
    # Airflow's run_id; stages 2, 3 and 6 get the same one.
    meta_required = ["run_id"]
  }

  group "discover" {
    count = 1

    restart {
      attempts = 0
      mode     = "fail"
    }
    # Retries are Airflow's job, not Nomad's.
    reschedule {
      attempts  = 0
      unlimited = false
    }

    task "discover" {
      driver = "docker"

      config {
        image   = "${var.image_repo}:${var.image_tag}"
        command = "python"
        args    = ["-m", "rag_discover", "--run-id", "${NOMAD_META_run_id}"]
        # local_pdfs in sources.yaml scans /mnt/zdrive/RAG-Data/ETFs
        volumes = ["/mnt/zdrive/:/mnt/zdrive"]
      }

      vault {}

      # Vault secrets + the rag-postgres address from Consul, rendered as env vars
      # (same pattern as rag-postgres.hcl), so the code only ever reads env vars.
      template {
        data        = <<EOT
{{- with secret "kv/data/secret/cluster/rag" }}
{{- $user := .Data.data.POSTGRES_USER }}{{ $pw := .Data.data.POSTGRES_PASSWORD }}
{{- range service "rag-postgres" }}
RAG_PG_DSN=postgresql+psycopg://{{ $user }}:{{ $pw | urlquery }}@{{ .Address }}:{{ .Port }}/rag_ingestion
{{- end }}
RAG_CONFLUENCE_EMAIL={{ .Data.data.RAG_CONFLUENCE_EMAIL }}
RAG_CONFLUENCE_API_TOKEN={{ .Data.data.RAG_CONFLUENCE_API_TOKEN }}
{{- end }}
EOT
        destination = "secrets/rag.env"
        env         = true
        # A batch run must not be restarted by a secret rotation mid-run.
        change_mode = "noop"
      }

      resources {
        cpu    = 1000
        memory = 1024
      }
    }
  }
}
