import { useState } from 'react'

// 아직 백엔드 저장 API가 없어서 화면 상태로만 들고 있음
export default function Targets() {
  const [aws, setAws] = useState({
    region: 'ap-northeast-2',
    roleArn: '',
    stateBucket: '',
  })
  const [onprem, setOnprem] = useState({
    host: '',
    port: '22',
    user: 'deploy',
    path: '/srv/apps',
  })
  const [saved, setSaved] = useState(false)

  const save = () => {
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="single narrow">
      <div className="page-head">
        <h1>배포 대상 설정</h1>
        <p>배포할 AWS 계정과 온프레미스 서버 접속 정보를 등록합니다. 키 값은 서버에만 저장됩니다.</p>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>AWS</h2>
          <span className="muted small">ECS Fargate, Terraform</span>
        </div>
        <div className="card-body form-grid">
          <div className="field">
            <label htmlFor="region">리전</label>
            <select
              id="region"
              className="input"
              value={aws.region}
              onChange={(e) => setAws({ ...aws, region: e.target.value })}
            >
              <option value="ap-northeast-2">ap-northeast-2 (서울)</option>
              <option value="ap-northeast-1">ap-northeast-1 (도쿄)</option>
              <option value="us-east-1">us-east-1 (버지니아)</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="role">배포용 IAM Role ARN</label>
            <input
              id="role"
              className="input mono"
              placeholder="arn:aws:iam::123456789012:role/oneship-deployer"
              value={aws.roleArn}
              onChange={(e) => setAws({ ...aws, roleArn: e.target.value })}
            />
          </div>
          <div className="field span-2">
            <label htmlFor="bucket">Terraform state 버킷</label>
            <input
              id="bucket"
              className="input mono"
              placeholder="oneship-tfstate"
              value={aws.stateBucket}
              onChange={(e) => setAws({ ...aws, stateBucket: e.target.value })}
            />
          </div>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>온프레미스</h2>
          <span className="muted small">Docker Compose, SSH</span>
        </div>
        <div className="card-body form-grid">
          <div className="field">
            <label htmlFor="host">호스트</label>
            <input
              id="host"
              className="input mono"
              placeholder="192.168.0.24"
              value={onprem.host}
              onChange={(e) => setOnprem({ ...onprem, host: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="port">SSH 포트</label>
            <input
              id="port"
              className="input mono"
              value={onprem.port}
              onChange={(e) => setOnprem({ ...onprem, port: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="user">사용자</label>
            <input
              id="user"
              className="input mono"
              value={onprem.user}
              onChange={(e) => setOnprem({ ...onprem, user: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="path">배포 경로</label>
            <input
              id="path"
              className="input mono"
              value={onprem.path}
              onChange={(e) => setOnprem({ ...onprem, path: e.target.value })}
            />
          </div>
          <div className="field span-2">
            <label htmlFor="key">SSH 개인 키</label>
            <textarea id="key" className="input mono" rows={4} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
          </div>
        </div>
      </section>

      <div className="form-actions">
        <span className="hint">{saved ? '저장했습니다.' : ''}</span>
        <button className="btn btn-primary" onClick={save}>
          저장
        </button>
      </div>
    </div>
  )
}
