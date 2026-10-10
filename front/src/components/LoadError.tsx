/** 조회 실패. 빈 목록과 구분해서 보여 주고 다시 불러올 수 있게 함 */
export default function LoadError({ what, message, onRetry }: { what: string; message: string; onRetry: () => void }) {
  return (
    <div className="load-error" role="alert">
      <div>
        <strong>{what}을 불러오지 못했습니다.</strong>
        <span>{message}</span>
      </div>
      <button className="btn btn-sm" onClick={onRetry}>
        다시 불러오기
      </button>
    </div>
  )
}
